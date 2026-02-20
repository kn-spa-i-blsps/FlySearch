import json
from pathlib import Path
from PIL import Image

from conversation.abstract_conversation import Role
from conversation.conversations import LLM_BACKEND_FACTORIES
from mission_control.core.exceptions import ChatSessionError, ChatSaveError, ChatRestoreError


class ChatSessionManager:

    def __init__(self, config, mission_context):
        self.config = config
        self.mission_context = mission_context

    async def create_new_session(self):
        """ Creates a new chat session, raising errors if preconditions are not met. """
        if self.mission_context.conversation is not None:
            raise ChatSessionError("Chat already exists. Use CHAT_RESET to delete the chat first.")

        if self.mission_context.last_prompt_text_cache is None:
            raise ChatSessionError("No prompt generated yet. Use PROMPT command to generate one.")

        # Get the proper LLM backend using factories.
        factory = LLM_BACKEND_FACTORIES[self.config.model_backend](self.config.model_name)
        self.mission_context.conversation = factory.get_conversation()
        self.mission_context.conversation.begin_transaction(Role.USER)  # Chat initialization.
        self.mission_context.conversation.add_text_message(self.mission_context.last_prompt_text_cache)
        print("New chat session created successfully.")

    async def save_session(self, chat_id):
        """ Serializes and saves the current chat history - prompts and images to disk. """
        if self.mission_context.conversation is None:
            raise ChatSessionError("Chat with VLM is not initialized. Use CHAT_INIT first.")

        chat_dir = self.config.chats_dir / chat_id
        assets_dir = chat_dir / "assets"

        try:
            assets_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ChatSaveError(f"Failed to create chat directories at {chat_dir}: {e}") from e

        serializable_history = []
        image_counter = 0

        history = self.mission_context.conversation.get_conversation()
        for role, content_data in history:
            role_str = role.value if hasattr(role, "value") else str(role)
            serializable_content = {"role": role_str, "parts": []}

            if isinstance(content_data, str):
                serializable_content["parts"].append({"type": "text", "data": content_data})
            else:
                try:
                    image = content_data
                    if image.mode in ("RGBA", "P"):
                        image = image.convert("RGB")

                    ext = "jpg"
                    filename = f"image_{image_counter}.{ext}"
                    file_path = assets_dir / filename
                    image.save(file_path, format="JPEG", quality=95)
                    relative_path = str(assets_dir.name + "/" + filename)
                    serializable_content["parts"].append({"type": "image", "path": relative_path})
                    image_counter += 1
                except Exception as e:
                    raise ChatSaveError(f"Error processing and saving image for message {role_str}: {e}") from e

            serializable_history.append(serializable_content)

        json_path = chat_dir / "history.json"
        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(serializable_history, f, indent=2, ensure_ascii=False)
            print(f"Successfully saved history to {json_path}")
            print(f"Successfully saved {image_counter} images to {assets_dir}")
        except (IOError, json.JSONDecodeError) as e:
            raise ChatSaveError(f"Error writing JSON file {json_path}: {e}") from e

    async def restore_session(self, chat_id):
        """ Reconstructs and resumes a previously saved chat session. """
        chat_dir = self.config.chats_dir / chat_id
        json_path = chat_dir / "history.json"

        if not json_path.exists():
            raise ChatRestoreError(f"Chat history not found at {json_path}")

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            raise ChatRestoreError(f"Failed to read or parse chat history from {json_path}: {e}") from e

        factory = LLM_BACKEND_FACTORIES[self.config.model_backend](self.config.model_name)
        conversation = factory.get_conversation()

        try:
            for message in history:
                role = Role(message['role'])
                conversation.begin_transaction(role)
                for part in message['parts']:
                    if part['type'] == 'text':
                        conversation.add_text_message(part['data'])
                    elif part['type'] == 'image':
                        image_path = chat_dir / part['path']
                        if image_path.exists():
                            img = Image.open(image_path)
                            conversation.add_image_message(img)
                        else:
                            print(f"Warning: Image not found at {image_path}, skipping.")
                conversation.commit_transaction(send_to_vlm=False)
        except (KeyError, ValueError) as e:
            raise ChatRestoreError(f"Corrupted data in chat history file: {e}") from e
        except IOError as e:
            raise ChatRestoreError(f"Failed to open image file during restore: {e}") from e
        
        self.mission_context.conversation = conversation
        print(f"Chat session '{chat_id}' restored successfully.")

    async def reset_session(self):
        """ Resets the active chat session (clears memory). """
        self.mission_context.conversation = None
