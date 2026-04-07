import asyncio
import json
import logging
from pathlib import Path
from typing import List, Dict, Any

import aiofiles
from PIL import Image

from mission_control.conversation.abstract_conversation import Conversation
from mission_control.core.exceptions import ChatSaveError, ChatRestoreError
from mission_control.core.interfaces import ChatStorageHelper

logger = logging.getLogger(__name__)


class FileChatStorageHelper(ChatStorageHelper):

    def __init__(self, chats_dir: Path):
        self.chats_dir = chats_dir

    async def save_chat(self, chat_id: str, conversation: Conversation) -> None:
        """ Serializes and saves the current chat history - prompts and images to disk. """
        chat_dir = self.chats_dir / chat_id
        assets_dir = chat_dir / "assets"

        try:
            assets_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ChatSaveError(f"Failed to create chat directories at {chat_dir}: {e}") from e

        serializable_history = []
        image_save_tasks = []
        image_counter = 0
        current_message = None

        history = conversation.get_conversation()

        for role, content_data in history:
            role_val = str(getattr(role, "value", role))

            if current_message is None or current_message["role"] != role_val:
                if current_message is not None:
                    serializable_history.append(current_message)
                current_message = {"role": role_val, "parts": []}

            if isinstance(content_data, str):
                current_message["parts"].append({"type": "text", "data": content_data})
            else:
                try:
                    image = content_data
                    if image.mode in ("RGBA", "P"):
                        image = image.convert("RGB")

                    filename = f"image_{image_counter}.jpg"
                    file_path = assets_dir / filename

                    image_save_tasks.append(
                        asyncio.to_thread(image.save, file_path, format="JPEG", quality=95)
                    )

                    relative_path = f"{assets_dir.name}/{filename}"
                    current_message["parts"].append({"type": "image", "path": relative_path})
                    image_counter += 1
                except Exception as e:
                    raise ChatSaveError(f"Error processing image for message {role_val}: {e}") from e

        if current_message is not None:
            serializable_history.append(current_message)

        json_path = chat_dir / "history.json"
        try:
            json_str = json.dumps(serializable_history, indent=2, ensure_ascii=False)
            async with aiofiles.open(json_path, mode='w', encoding='utf-8') as f:
                await f.write(json_str)

            if image_save_tasks:
                await asyncio.gather(*image_save_tasks)

            logger.info(f"Successfully saved history to {json_path}")
            logger.info(f"Successfully saved {image_counter} images to {assets_dir}")
        except (IOError, json.JSONDecodeError) as e:
            raise ChatSaveError(f"Error writing JSON file {json_path}: {e}") from e

    async def load_chat(self, chat_id: str) -> List[Dict[str, Any]]:
        """ Reconstructs and resumes a previously saved chat session. """
        chat_dir = self.chats_dir / chat_id
        json_path = chat_dir / "history.json"

        if not json_path.exists():
            raise ChatRestoreError(f"Chat history not found at {json_path}")

        try:
            async with aiofiles.open(json_path, mode='r', encoding='utf-8') as f:
                content = await f.read()
            history = json.loads(content)
        except (IOError, json.JSONDecodeError) as e:
            raise ChatRestoreError(f"Failed to read or parse chat history from {json_path}: {e}") from e

        reconstructed_history = []
        try:
            for message in history:
                role_str = message['role']
                loaded_parts = []

                for part in message['parts']:
                    if part['type'] == 'text':
                        loaded_parts.append({'type': 'text', 'data': part['data']})

                    elif part['type'] == 'image':
                        image_path = chat_dir / part['path']
                        if image_path.exists():
                            def load_image_sync(path):
                                img = Image.open(path)
                                img.load()
                                return img

                            img_obj = await asyncio.to_thread(load_image_sync, image_path)
                            loaded_parts.append({'type': 'image', 'data': img_obj})
                        else:
                            logger.warning(f"[CHAT STORAGE] Image not found at {image_path}, skipping.")

                reconstructed_history.append({'role': role_str, 'parts': loaded_parts})

        except (KeyError, ValueError) as e:
            raise ChatRestoreError(f"Corrupted data in chat history file: {e}") from e
        except IOError as e:
            raise ChatRestoreError(f"Failed to open image file during restore: {e}") from e

        logger.info(f"Chat session '{chat_id}' restored successfully.")
        return reconstructed_history