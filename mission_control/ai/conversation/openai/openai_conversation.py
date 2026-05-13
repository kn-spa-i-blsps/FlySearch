import asyncio
import base64
import io
from time import sleep

from PIL import Image
from openai import RateLimitError, Client

from mission_control.ai.conversation.abstract_conversation import Conversation, Role
from mission_control.utils.logger import get_configured_logger

logger = get_configured_logger(__name__)


class OpenAIConversation(Conversation):
    def __init__(self, client: Client, model_name: str, seed=42, max_tokens=300, temperature=0.8, top_p=1.0):
        self.client = client
        self.conversation = []
        self.model_name = model_name
        self.seed = seed
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p

        self.transaction_started = False
        self.transaction_role = None
        self.transaction_conversation = {}

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

    async def add_image_message(self, image: Image.Image):
        if not self.transaction_started:
            raise Exception("Transaction not started")

        image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=95)
        base64_image = base64.b64encode(buffer.getvalue()).decode('utf-8')

        content = self.transaction_conversation["content"]

        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_image}",
                    "detail": "high"  # FIXME
                }
            }
        )

    def get_answer_from_openai(self, model, messages, max_tokens, seed, temperature, top_p):
        fail = True
        response = None

        while fail:
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    # seed=seed,
                    temperature=temperature,
                    top_p=top_p
                )
                fail = False
            except RateLimitError as e:
                logger.warning("Rate limit error")
                logger.warning(e)
                sleep(120)
                fail = True
        return response

    async def commit_transaction(self, send_to_vlm=False):
        if not self.transaction_started:
            raise Exception("Transaction not started")

        self.conversation.append(self.transaction_conversation)
        self.transaction_conversation = {}

        if self.transaction_role == Role.ASSISTANT and send_to_vlm:
            raise Exception("Assistant cannot send messages to VLM")

        self.transaction_started = False
        self.transaction_role = None

        if not send_to_vlm:
            return

        response = await asyncio.to_thread(
            self.get_answer_from_openai,
            self.model_name,
            self.conversation,
            self.max_tokens,
            self.seed,
            self.temperature,
            self.top_p
        )

        response_content = str(response.choices[0].message.content)

        logger.info("llm response:", response_content)

        self.conversation.append({
            "role": "assistant",
            "content": response_content
        })

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

                        elif submessage["type"] == "image_url":
                            try:
                                url_string = submessage["image_url"]["url"]

                                if "base64," in url_string:
                                    base64_data = url_string.split("base64,")[1]

                                    image_bytes = base64.b64decode(base64_data)

                                    image = Image.open(io.BytesIO(image_bytes))

                                    image.load()

                                    yield role, image
                                else:
                                    yield role, url_string

                            except Exception as e:
                                logger.error(f"Error decoding image from history: {e}")
                                yield role, "image_error"
                else:
                    raise Exception("Invalid content type")

        return list(conversation_iterator())

    def get_latest_message(self):
        if len(self.conversation) == 0:
            raise Exception("No messages in conversation")

        return self.get_conversation()[-1]

    def get_entire_conversation(self):
        return self.conversation
