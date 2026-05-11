import io
import logging
from time import sleep

from PIL import Image
from google import genai
from google.genai import types
from google.genai.errors import APIError, ServerError

from mission_control.ai.conversation.abstract_conversation import Conversation, Role


class GeminiConversation(Conversation):
    def __init__(self, client: genai.Client, model_name: str, seed=42, max_tokens=None, temperature=None, top_p=None,
                 thinking_budget=None):
        self.client = client
        self.model_name = model_name
        self.conversation = []  # This will be populated from chat history
        self.seed = seed
        self.max_tokens = max_tokens  # maps to max_output_tokens in Gemini
        self.temperature = temperature
        self.top_p = top_p
        self.thinking_budget = thinking_budget
        self.logger = logging.getLogger(__name__)

        self.chat = self.client.chats.create(
            model=self.model_name,
            config=self._get_generation_config()
        )

        self.transaction_started = False
        self.transaction_role = None
        self.transaction_conversation = {}
        self._chat_history_synced = True

    def begin_transaction(self, role: Role):
        if self.transaction_started:
            raise Exception("Transaction already started")

        self.transaction_started = True
        self.transaction_role = role

        role = "user" if role == Role.USER else "assistant"

        self.transaction_conversation = {
            "role": role,
            "content": []
        }

    def add_text_message(self, text: str):
        if not self.transaction_started:
            raise Exception("Transaction not started")

        if self.transaction_conversation['role'] == 'assistant':
            self.transaction_conversation['content'] = text
        else:
            content = self.transaction_conversation["content"]
            content.append({
                "type": "text",
                "text": text
            })

    def add_image_message(self, image: Image.Image):
        if not self.transaction_started:
            raise Exception("Transaction not started")

        image = image.convert("RGB")

        content = self.transaction_conversation["content"]

        content.append(
            {
                "type": "image",
                "image": image,
            }
        )

    def _to_gemini_parts(self, message_content):
        parts = []
        if isinstance(message_content, str):
            return [types.Part.from_text(text=message_content)]
        elif isinstance(message_content, list):
            for sub in message_content:
                if sub["type"] == "text":
                    parts.append(types.Part.from_text(text=sub["text"]))
                elif sub["type"] == "image":
                    # Convert PIL Image to bytes
                    img = sub["image"]
                    buffer = io.BytesIO()
                    img.save(buffer, format='JPEG', quality=95)
                    buffer.seek(0)
                    parts.append(types.Part.from_bytes(
                        data=buffer.read(),
                        mime_type='image/jpeg'
                    ))
                else:
                    parts.append(types.Part.from_text("[unsupported content]"))
            return parts
        else:
            return [types.Part.from_text("[unsupported content]")]

    def _get_generation_config(self):
        config_dict = {}
        if self.max_tokens is not None:
            config_dict["max_output_tokens"] = self.max_tokens
        if self.temperature is not None:
            config_dict["temperature"] = self.temperature
        if self.top_p is not None:
            config_dict["top_p"] = self.top_p
        if self.thinking_budget is not None:
            config_dict["thinking_config"] = types.ThinkingConfig(thinking_budget=self.thinking_budget)
        return types.GenerateContentConfig(**config_dict) if config_dict else None

    def _send_message_with_retry(self, parts):
        retries = 3
        delay = 5  # seconds
        for i in range(retries):
            try:
                response = self.chat.send_message(
                    message=parts
                )
                return response
            except (APIError, ServerError) as e:
                # Using 429 and 499 for rate limiting, but being broad for other transient issues
                if e.code in [429, 499, 500, 503, 504]:
                    self.logger.warning(f"APIError received: {e}. Retrying in {delay} seconds...")
                    sleep(delay)
                    delay *= 2  # Exponential backoff
                else:
                    self.logger.error(f"Unhandled APIError: {e}")
                    raise e
            except Exception as e:
                self.logger.error(f"An unexpected error occurred: {e}")
                raise e
        raise Exception("Failed to get response after multiple retries")

    def commit_transaction(self, send_to_vlm=False):
        if not self.transaction_started:
            raise Exception("Transaction not started")

        message_to_commit = self.transaction_conversation
        self.conversation.append(message_to_commit)

        self.transaction_conversation = {}
        self.transaction_started = False

        role = self.transaction_role
        self.transaction_role = None

        if not send_to_vlm:
            self._chat_history_synced = False
            return

        if role == Role.ASSISTANT and send_to_vlm:
            raise Exception("Assistant cannot send messages to VLM")

        if not self._chat_history_synced:
            self._rebuild_chat_with_history()

        # Get the message parts from the just committed message
        msg = self.conversation[-1]
        parts = self._to_gemini_parts(msg["content"])

        response = self._send_message_with_retry(parts)

        response_content = str(response.text)

        self.logger.info(f"LLM response: {response_content}")

        # Add the model's response to the history
        response_message = {
            "role": "assistant",
            "content": response_content
        }
        self.conversation.append(response_message)

    def _rebuild_chat_with_history(self):
        history = []
        for msg in self.conversation[:-1]:
            role = "user" if msg["role"] == "user" else "model"
            parts = self._to_gemini_parts(msg["content"])
            history.append(types.Content(role=role, parts=parts))

        self.chat = self.client.chats.create(
            model=self.model_name,
            config=self._get_generation_config(),
            history=history
        )
        self._chat_history_synced = True

    def rollback_transaction(self):
        self.transaction_conversation = {}

        self.transaction_started = False
        self.transaction_role = None

    def get_conversation(self, save_urls=True):
        def conversation_iterator():
            for message in self.conversation:
                role = Role.USER if message["role"] == "user" else Role.ASSISTANT
                content = message["content"]

                if isinstance(content, str):
                    yield role, content
                elif isinstance(content, list):
                    for submessage in content:
                        if submessage["type"] == "text":
                            yield role, submessage["text"]
                        elif submessage["type"] == "image":
                            yield role, submessage["image"]
                else:
                    raise Exception("Invalid content type")

        return list(conversation_iterator())

    def get_latest_message(self):
        if len(self.conversation) == 0:
            raise Exception("No messages in conversation")

        return self.get_conversation()[-1]

    def get_entire_conversation(self):
        return self.conversation
