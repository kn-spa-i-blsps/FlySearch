import asyncio
import traceback
from pathlib import Path
from typing import Tuple, List, Dict, Any

from PIL import Image

from mission_control.ai.conversation.abstract_conversation import Role, Conversation
from mission_control.ai.conversation.conversations import LLM_BACKEND_FACTORIES
from mission_control.core.config import Config
from mission_control.core.events import AnalyzePhotoCommand, VlmAnalysisCompleted, VlmErrorOccurred, \
    CreateNewSessionCommand, NewSessionCreated, ChatErrorOccurred, DeleteSessionCommand, SessionDeleted, \
    SaveSessionCommand, LoadSessionCommand, SessionSaved, SessionLoaded
from mission_control.core.exceptions import VLMConnectionError
from mission_control.core.interfaces import ChatStorageHelper, VLMBridge
from mission_control.core.event_bus import EventBus
from mission_control.utils.image_processing import add_grid_async
from mission_control.utils.logger import get_configured_logger
from mission_control.utils.parsers import parse_xml_response, ModelResponse, \
    get_height_async

logger = get_configured_logger(__name__)


class FlySearchVLMBridge(VLMBridge):
    """ Bridge for the communication between the server and the VLM. """

    def __init__(self, config: Config, event_bus: EventBus, storage: ChatStorageHelper):
        self.config = config
        self.event_bus = event_bus
        self.collision_warning_str = "Your move would cause a collision. Make another move."
        self.conversations = {}
        self.chat_locks = {}
        self.storage = storage

        self.event_bus.subscribe(AnalyzePhotoCommand, self.handle_analyze_photo)
        self.event_bus.subscribe(CreateNewSessionCommand, self.handle_create_new_session)
        self.event_bus.subscribe(DeleteSessionCommand, self.handle_delete_session)
        self.event_bus.subscribe(SaveSessionCommand, self.handle_save_session)
        self.event_bus.subscribe(LoadSessionCommand, self.handle_load_session)

    async def handle_analyze_photo(self, event: AnalyzePhotoCommand) -> None:
        """
        Prepares and sends the current context (image, telemetry, prompts) to the Vision Language Model.
        Handler for AnalyzePhotoCommand.

        :publishes:
            VlmAnalysisCompleted on success.
            VlmErrorOccurred on error.
        """
        chat_id = event.chat_id
        is_warning = event.is_warning
        photo_path = event.photo_path
        telemetry_path = event.telemetry_path

        try:
            if chat_id not in self.conversations:
                raise VLMConnectionError(f"Chat with id {chat_id} is not initialized. "
                                         f"Use CHAT_INIT first.")

            if chat_id not in self.chat_locks:
                self.chat_locks[chat_id] = asyncio.Lock()

            async with self.chat_locks[chat_id]:
                conversation = self.conversations[chat_id]
                img, message = await self._prepare_input_async(photo_path, telemetry_path)
                raw_response = await self._execute_transaction(conversation, img, message, is_warning)

                # Fast, so shouldn't be problematic
                parsed = self._parse_xml_response_sync(raw_response)

            analysis = VlmAnalysisCompleted(
                chat_id=chat_id,
                reasoning=parsed.reasoning,
                move=parsed.move,
                found=parsed.found
            )
            await self.event_bus.publish(analysis)

        except Exception as e:
            err_event = VlmErrorOccurred(
                chat_id=chat_id,
                error_message=f"[VLM] Analysis failed: {str(e)}",
                traceback=traceback.format_exc()
            )
            await self.event_bus.publish(err_event)
            logger.error(f"[VLM] Analysis failed: {e}")

    async def handle_create_new_session(self, event: CreateNewSessionCommand):
        """
        Creates a new chat session with specified chat_id. Handler for CreateNewSessionCommand.

        :publishes:
            NewSessionCreated on success.
            ChatErrorOccurred on error.
        """

        chat_id = event.chat_id
        prompt = event.prompt
        try:
            if chat_id in self.conversations:
                raise VLMConnectionError(f"Chat with id {chat_id} already exists. "
                                         f"Use CHAT_RESET to delete the chat first.")
            conversation = self._create_empty_conversation()
            conversation.begin_transaction(Role.USER)
            conversation.add_text_message(prompt)
            await conversation.commit_transaction(send_to_vlm=False)
            self.conversations[chat_id] = conversation
            self.chat_locks[chat_id] = asyncio.Lock()
            logger.info("[VLM] New chat session created successfully.")
            await self.event_bus.publish(NewSessionCreated(chat_id=chat_id))

        except Exception as e:
            err_event = ChatErrorOccurred(
                chat_id=chat_id,
                error_message=f"[VLM] Chat creation failed: {str(e)}",
                traceback=traceback.format_exc()
            )
            await self.event_bus.publish(err_event)
            logger.error(f"[VLM] Chat creation failed: {e}")

    async def handle_delete_session(self, event: DeleteSessionCommand):
        """
        Deletes session with specified chat_id. Handler for DeleteSessionCommand.

        :publishes:
            SessionDeleted on success.
        """
        chat_id = event.chat_id
        self.conversations.pop(chat_id, None)
        self.chat_locks.pop(chat_id, None)
        logger.info("[VLM] Chat session deleted successfully.")
        await self.event_bus.publish(SessionDeleted(chat_id=chat_id))

    async def handle_save_session(self, event: SaveSessionCommand):
        """
        Saves session using given storage helper. Handler for SaveSessionCommand.

        :publishes:
            SessionSaved on success.
            ChatErrorOccurred on error.
        """
        chat_id = event.chat_id
        try:
            if chat_id not in self.conversations:
                raise VLMConnectionError(f"Chat with id {chat_id} is not initialized. "
                                         f"Use CHAT_INIT first.")

            if chat_id not in self.chat_locks:
                self.chat_locks[chat_id] = asyncio.Lock()

            async with self.chat_locks[chat_id]:
                conversation = self.conversations[chat_id]
                await self.storage.save_chat(chat_id, conversation)

            await self.event_bus.publish(SessionSaved(chat_id=chat_id))
        except Exception as e:
            err_event = ChatErrorOccurred(
                chat_id=chat_id,
                error_message=f"[VLM] Saving chat failed: {str(e)}",
                traceback=traceback.format_exc()
            )
            await self.event_bus.publish(err_event)
            logger.error(f"[VLM] Saving chat failed: {e}")

    async def handle_load_session(self, event: LoadSessionCommand):
        """
        Loads session using given storage helper. Handler for SaveSessionCommand.

        :publishes:
            SessionLoaded on success.
            ChatErrorOccurred on error.
        """
        chat_id = event.chat_id
        try:
            if chat_id in self.conversations:
                raise VLMConnectionError(f"Chat with id {chat_id} already exists. "
                                         f"Use CHAT_RESET to delete the chat first.")
            if chat_id not in self.chat_locks:
                self.chat_locks[chat_id] = asyncio.Lock()

            async with self.chat_locks[chat_id]:
                conversation_list = await self.storage.load_chat(chat_id)
                self.conversations[chat_id] = await self._list_to_conversation(conversation_list)
            await self.event_bus.publish(SessionLoaded(chat_id=chat_id))
        except Exception as e:
            err_event = ChatErrorOccurred(
                chat_id=chat_id,
                error_message=f"[VLM] Loading chat failed: {str(e)}",
                traceback=traceback.format_exc()
            )
            await self.event_bus.publish(err_event)
            logger.error(f"[VLM] Loading chat failed: {e}")

    ''' ------------------------------------------------------------------------- '''

    def _create_empty_conversation(self):
        factory = LLM_BACKEND_FACTORIES[self.config.model_backend](self.config.model_name)
        return factory.get_conversation()

    async def _list_to_conversation(self, raw_history: List[Dict[str, Any]]) -> Conversation:
        conversation = self._create_empty_conversation()

        for message in raw_history:
            role = Role(message['role'])

            conversation.begin_transaction(role)
            for part in message['parts']:
                if part['type'] == 'text':
                    conversation.add_text_message(part['data'])
                elif part['type'] == 'image':
                    await conversation.add_image_message(part['data'])

            await conversation.commit_transaction(send_to_vlm=False)

        return conversation

    @staticmethod
    async def _prepare_input_async(photo_path: Path, telemetry_path: Path) -> Tuple[Image.Image, str]:
        drone_height = await get_height_async(telemetry_path)
        message = f"Your current altitude is {drone_height} meters above ground level."
        img = await add_grid_async(photo_path, drone_height)
        return img, message

    async def _execute_transaction(self, conversation: Conversation, img: Image.Image,
                                   message: str, is_warning: bool) -> str:
        try:
            conversation.begin_transaction(Role.USER)

            if is_warning:
                # Warning: Warning text + image with a grid + telemetry context
                conversation.add_text_message(self.collision_warning_str)

            # Standard Step: image with a grid + telemetry context
            await conversation.add_image_message(img)
            conversation.add_text_message(message)

            # Send message.
            await conversation.commit_transaction(send_to_vlm=True)

            role, message = conversation.get_latest_message()
        except Exception as e:
            conversation.rollback_transaction()
            raise VLMConnectionError(f"Message sending to the VLM failed.") from e

        if role == Role.ASSISTANT:
            return str(message)

        raise VLMConnectionError("[VLM] No response from VLM.")

    @staticmethod
    def _parse_xml_response_sync(raw_response: str) -> ModelResponse:
        return parse_xml_response(raw_response)
