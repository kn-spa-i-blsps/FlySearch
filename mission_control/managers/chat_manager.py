import json
from pathlib import Path
from PIL import Image

from conversation.abstract_conversation import Role
from conversation.conversations import LLM_BACKEND_FACTORIES


class ChatSessionManager:

    def __init__(self, config, mission_context):
        self.config = config
        self.mission_context = mission_context

    async def create_new_session(self):
        """"""
        if self.mission_context.conversation is not None:
            print("Chat already exists. Use CHAT_DELETE to delete the chat first.")
            return

        if self.mission_context.last_prompt_text_cache is None:
            print("No prompt generated yet. Use PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..].")
            return

        # Get the proper LLM backend using factories.
        factory = LLM_BACKEND_FACTORIES[self.config.model_backend](self.config.model_name)
        self.mission_context.conversation = factory.get_conversation()
        self.mission_context.conversation.begin_transaction(Role.USER)  # Chat initialization.
        self.mission_context.conversation.add_text_message(self.mission_context.last_prompt_text_cache)
        return

    async def save_session(self, chat_id):
        """ Serializes and saves the current chat history - prompts and images to disk.

        :param chat_id: The unique identifier/name for the chat directory.

        Result:
            Creates a directory 'CHATS_DIR/chat_id' containing:
            - assets/: Directory containing all images from the conversation.
            - history.json: JSON file containing the message history with references to assets.
        """

        if self.mission_context.conversation is None:
            print("Chat with VLM is not initialized. Use CHAT_INIT first.")
            return

        chat_dir = self.config.chats_dir / chat_id
        assets_dir = chat_dir / "assets"

        assets_dir.mkdir(parents=True, exist_ok=True)

        serializable_history = []
        image_counter = 0

        history = self.mission_context.conversation.get_conversation()
        for role, content_data in history:

            role_str = role.value if hasattr(role, "value") else str(role)

            serializable_content = {
                "role": role_str,
                "parts": []
            }

            if isinstance(content_data, str):
                serializable_content["parts"].append({
                    "type": "text",
                    "data": content_data
                })

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

                    serializable_content["parts"].append({
                        "type": "image",
                        "path": relative_path
                    })

                    image_counter += 1

                except Exception as e:
                    print(f"Error processing image for message {role_str}: {e}")
                    continue

            serializable_history.append(serializable_content)

        json_path = chat_dir / "history.json"
        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(serializable_history, f, indent=2, ensure_ascii=False)
            print(f"Successfully saved history to {json_path}")
            print(f"Successfully saved {image_counter} images to {assets_dir}")
        except Exception as e:
            print(f"Error writing JSON file {json_path}: {e}")

    async def restore_session(self, chat_id):
        """
        Reconstructs and resumes a previously saved chat session.

        Reads 'history.json', loads referenced images from the disk,
        and initializes the VLM chat with the restored history.
        """
        chat_dir = self.config.chats_dir / chat_id
        json_path = chat_dir / "history.json"

        if not json_path.exists():
            print(f"Error: Chat history not found at {json_path}")
            return

        with open(json_path, 'r', encoding='utf-8') as f:
            history = json.load(f)

        factory = LLM_BACKEND_FACTORIES[self.config.model_backend](self.config.model_name)
        conversation = factory.get_conversation()

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
                        print(f"Warning: Image not found at {image_path}")
            conversation.end_transaction()
        
        self.mission_context.conversation = conversation
        print(f"Chat session '{chat_id}' restored successfully.")


    async def reset_session(self):
        """ Resets the active chat session (clears memory).

        Requires user confirmation via CLI input.
        """

        self.mission_context.conversation = None
