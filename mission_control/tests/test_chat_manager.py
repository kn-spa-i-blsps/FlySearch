
import unittest
from unittest.mock import MagicMock, patch, mock_open, call
import json
from pathlib import Path

from mission_control.managers.chat_manager import ChatSessionManager
from mission_control.core.exceptions import ChatSessionError, ChatSaveError, ChatRestoreError
from conversation.abstract_conversation import Role

class TestChatSessionManager(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_config = MagicMock()
        self.mock_config.chats_dir = Path('/fake/chats')
        self.mock_config.model_backend = 'mock_backend'
        self.mock_config.model_name = 'mock_model'
        
        self.mock_mission_context = MagicMock()
        self.mock_mission_context.conversation = None
        self.mock_mission_context.last_prompt_text_cache = None

        self.chat_manager = ChatSessionManager(self.mock_config, self.mock_mission_context)

    @patch('mission_control.managers.chat_manager.LLM_BACKEND_FACTORIES')
    async def test_create_new_session_success(self, mock_llm_factories):
        """Test successful creation of a new chat session."""
        self.mock_mission_context.last_prompt_text_cache = "Initial prompt"
        
        mock_conversation = MagicMock()
        mock_factory_instance = MagicMock()
        mock_factory_instance.get_conversation.return_value = mock_conversation
        mock_llm_factories.__getitem__.return_value.return_value = mock_factory_instance

        await self.chat_manager.create_new_session()

        mock_llm_factories.__getitem__.assert_called_with('mock_backend')
        mock_llm_factories.__getitem__.return_value.assert_called_with('mock_model')
        self.assertIsNotNone(self.mock_mission_context.conversation)
        mock_conversation.begin_transaction.assert_called_with(Role.USER)
        mock_conversation.add_text_message.assert_called_with("Initial prompt")

    async def test_create_new_session_already_exists_raises_error(self):
        """Test create_new_session raises ChatSessionError if a session already exists."""
        self.mock_mission_context.conversation = MagicMock()
        with self.assertRaisesRegex(ChatSessionError, "Chat already exists"):
            await self.chat_manager.create_new_session()

    async def test_create_new_session_no_prompt_raises_error(self):
        """Test create_new_session raises ChatSessionError if no prompt is cached."""
        with self.assertRaisesRegex(ChatSessionError, "No prompt generated yet"):
            await self.chat_manager.create_new_session()

    async def test_save_session_no_conversation_raises_error(self):
        """Test save_session raises ChatSessionError when no chat is initialized."""
        with self.assertRaisesRegex(ChatSessionError, "Chat with VLM is not initialized"):
            await self.chat_manager.save_session("test_chat")

    @patch('builtins.open', new_callable=mock_open)
    @patch('json.dump')
    @patch('pathlib.Path.mkdir')
    async def test_save_session_success(self, mock_mkdir, mock_json_dump, mock_file_open):
        """Test successful saving of a chat session with text and images."""
        mock_conversation = MagicMock()
        mock_image = MagicMock()
        mock_image.mode = 'RGB'
        
        history = [
            (Role.USER, "This is the prompt."),
            (Role.ASSISTANT, mock_image)
        ]
        mock_conversation.get_conversation.return_value = history
        self.mock_mission_context.conversation = mock_conversation

        chat_id = "test_chat_id"
        await self.chat_manager.save_session(chat_id)

        chat_dir = self.mock_config.chats_dir / chat_id
        assets_dir = chat_dir / "assets"
        
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
        
        mock_image.save.assert_called_once_with(assets_dir / 'image_0.jpg', format='JPEG', quality=95)

        expected_history = [
            {
                "role": "user",
                "parts": [{"type": "text", "data": "This is the prompt."}]
            },
            {
                "role": "assistant",
                "parts": [{"type": "image", "path": "assets/image_0.jpg"}]
            }
        ]
        
        json_path = chat_dir / "history.json"
        mock_file_open.assert_called_once_with(json_path, 'w', encoding='utf-8')
        mock_json_dump.assert_called_once_with(expected_history, mock_file_open(), indent=2, ensure_ascii=False)

    @patch('builtins.open', new_callable=mock_open)
    @patch('json.dump')
    @patch('pathlib.Path.mkdir')
    async def test_save_session_with_rgba_image_conversion(self, mock_mkdir, mock_json_dump, mock_file_open):
        """Test save_session converts RGBA image to RGB."""
        mock_conversation = MagicMock()
        mock_image = MagicMock()
        mock_image.mode = 'RGBA'
        
        # Mock the converted image
        mock_converted_image = MagicMock()
        mock_image.convert.return_value = mock_converted_image

        history = [(Role.ASSISTANT, mock_image)]
        mock_conversation.get_conversation.return_value = history
        self.mock_mission_context.conversation = mock_conversation

        await self.chat_manager.save_session("test_chat_rgba")

        mock_image.convert.assert_called_once_with('RGB')
        mock_converted_image.save.assert_called_once()

    @patch('pathlib.Path.mkdir', side_effect=OSError("Permission denied"))
    async def test_save_session_mkdir_fails_raises_error(self, mock_mkdir):
        """Test save_session raises ChatSaveError if directory creation fails."""
        self.mock_mission_context.conversation = MagicMock()
        self.mock_mission_context.conversation.get_conversation.return_value = []
        with self.assertRaisesRegex(ChatSaveError, "Failed to create chat directories"):
            await self.chat_manager.save_session("test_chat")

    @patch('builtins.open', new_callable=mock_open)
    @patch('json.dump', side_effect=IOError("Disk full"))
    @patch('pathlib.Path.mkdir')
    async def test_save_session_json_write_fails_raises_error(self, mock_mkdir, mock_json_dump, mock_file_open):
        """Test save_session raises ChatSaveError if JSON writing fails."""
        self.mock_mission_context.conversation = MagicMock()
        self.mock_mission_context.conversation.get_conversation.return_value = []
        with self.assertRaisesRegex(ChatSaveError, "Error writing JSON file"):
            await self.chat_manager.save_session("test_chat")

    @patch('mission_control.managers.chat_manager.Image.open')
    @patch('mission_control.managers.chat_manager.Path.exists')
    @patch('builtins.open', new_callable=mock_open)
    @patch('mission_control.managers.chat_manager.LLM_BACKEND_FACTORIES')
    async def test_restore_session_success(self, mock_llm_factories, mock_file_open, mock_path_exists, mock_image_open):
        """Test successful restoration of a chat session."""
        chat_id = "restored_chat"
        chat_dir = self.mock_config.chats_dir / chat_id
        json_path = chat_dir / "history.json"
        image_path = chat_dir / "assets/image_0.jpg"

        mock_path_exists.side_effect = [True, True]

        history_data = [
            {"role": "user", "parts": [{"type": "text", "data": "Restored prompt"}]},
            {"role": "assistant", "parts": [{"type": "image", "path": "assets/image_0.jpg"}]}
        ]
        mock_file_open.return_value.read.return_value = json.dumps(history_data)

        mock_conversation = MagicMock()
        if not hasattr(mock_conversation, 'add_image_message'):
            mock_conversation.add_image_message = MagicMock()

        mock_factory_instance = MagicMock()
        mock_factory_instance.get_conversation.return_value = mock_conversation
        mock_llm_factories.__getitem__.return_value.return_value = mock_factory_instance
        
        mock_image = MagicMock()
        mock_image_open.return_value = mock_image

        await self.chat_manager.restore_session(chat_id)

        mock_file_open.assert_called_with(json_path, 'r', encoding='utf-8')
        self.assertEqual(self.mock_mission_context.conversation, mock_conversation)
        
        calls = [
            call.begin_transaction(Role.USER),
            call.add_text_message("Restored prompt"),
            call.commit_transaction(send_to_vlm=False),
            call.begin_transaction(Role.ASSISTANT),
            call.add_image_message(mock_image),
            call.commit_transaction(send_to_vlm=False)
        ]
        mock_conversation.assert_has_calls(calls, any_order=False)
        mock_image_open.assert_called_once_with(image_path)

    @patch('mission_control.managers.chat_manager.Image.open')
    @patch('mission_control.managers.chat_manager.Path.exists')
    @patch('builtins.open', new_callable=mock_open)
    @patch('mission_control.managers.chat_manager.LLM_BACKEND_FACTORIES')
    async def test_restore_session_missing_image_file(self, mock_llm_factories, mock_file_open, mock_path_exists, mock_image_open):
        """Test restore_session handles a missing image file gracefully."""
        chat_id = "missing_image_chat"
        chat_dir = self.mock_config.chats_dir / chat_id
        json_path = chat_dir / "history.json"

        # history.json exists, but the image does not
        mock_path_exists.side_effect = [True, False]

        history_data = [
            {"role": "assistant", "parts": [{"type": "image", "path": "assets/non_existent_image.jpg"}]}
        ]
        mock_file_open.return_value.read.return_value = json.dumps(history_data)

        mock_conversation = MagicMock()
        mock_factory_instance = MagicMock()
        mock_factory_instance.get_conversation.return_value = mock_conversation
        mock_llm_factories.__getitem__.return_value.return_value = mock_factory_instance

        await self.chat_manager.restore_session(chat_id)

        mock_image_open.assert_not_called()
        mock_conversation.add_image_message.assert_not_called()
        # Verify the session was still restored
        self.assertEqual(self.mock_mission_context.conversation, mock_conversation)

    @patch('mission_control.managers.chat_manager.Path.exists', return_value=False)
    async def test_restore_session_not_found_raises_error(self, mock_path_exists):
        """Test restore_session raises ChatRestoreError if history file is not found."""
        with self.assertRaisesRegex(ChatRestoreError, "Chat history not found"):
            await self.chat_manager.restore_session("non_existent_chat")

    @patch('mission_control.managers.chat_manager.Path.exists', return_value=True)
    @patch('builtins.open', mock_open(read_data='invalid json'))
    async def test_restore_session_invalid_json_raises_error(self, mock_path_exists):
        """Test restore_session raises ChatRestoreError for invalid JSON."""
        with self.assertRaisesRegex(ChatRestoreError, "Failed to read or parse chat history"):
            await self.chat_manager.restore_session("bad_json_chat")

    @patch('mission_control.managers.chat_manager.Path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open)
    @patch('mission_control.managers.chat_manager.LLM_BACKEND_FACTORIES')
    async def test_restore_session_corrupted_data_raises_error(self, mock_llm_factories, mock_file_open, mock_path_exists):
        """Test restore_session raises ChatRestoreError for corrupted data."""
        # Missing 'parts' key
        corrupted_data = [{"role": "user"}] 
        mock_file_open.return_value.read.return_value = json.dumps(corrupted_data)

        mock_factory_instance = MagicMock()
        mock_llm_factories.__getitem__.return_value.return_value = mock_factory_instance

        with self.assertRaisesRegex(ChatRestoreError, "Corrupted data in chat history file"):
            await self.chat_manager.restore_session("corrupted_chat")

    @patch('mission_control.managers.chat_manager.Path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open)
    @patch('mission_control.managers.chat_manager.LLM_BACKEND_FACTORIES')
    async def test_restore_session_invalid_role_raises_error(self, mock_llm_factories, mock_file_open, mock_path_exists):
        """Test restore_session raises ChatRestoreError for invalid role."""
        invalid_role_data = [{"role": "invalid_role", "parts": []}]
        mock_file_open.return_value.read.return_value = json.dumps(invalid_role_data)

        mock_factory_instance = MagicMock()
        mock_llm_factories.__getitem__.return_value.return_value = mock_factory_instance

        with self.assertRaisesRegex(ChatRestoreError, "Corrupted data in chat history file"):
            await self.chat_manager.restore_session("invalid_role_chat")

    async def test_reset_session(self):
        """Test that the session is correctly reset."""
        self.mock_mission_context.conversation = MagicMock()
        await self.chat_manager.reset_session()
        self.assertIsNone(self.mock_mission_context.conversation)

if __name__ == '__main__':
    unittest.main()
