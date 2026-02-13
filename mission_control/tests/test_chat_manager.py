
import unittest
from unittest.mock import MagicMock, patch, mock_open, call
import json
from pathlib import Path

from mission_control.managers.chat_manager import ChatSessionManager
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

    @patch('builtins.print')
    @patch('mission_control.managers.chat_manager.LLM_BACKEND_FACTORIES')
    async def test_create_new_session_success(self, mock_llm_factories, mock_print):
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

    @patch('builtins.print')
    async def test_create_new_session_already_exists(self, mock_print):
        """Test that a new session is not created if one already exists."""
        self.mock_mission_context.conversation = MagicMock()
        await self.chat_manager.create_new_session()
        mock_print.assert_called_with("Chat already exists. Use CHAT_DELETE to delete the chat first.")

    @patch('builtins.print')
    async def test_create_new_session_no_prompt(self, mock_print):
        """Test that a new session is not created if no prompt is cached."""
        await self.chat_manager.create_new_session()
        mock_print.assert_called_with("No prompt generated yet. Use PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..].")

    @patch('builtins.print')
    async def test_save_session_no_conversation(self, mock_print):
        """Test save_session when no chat is initialized."""
        await self.chat_manager.save_session("test_chat")
        mock_print.assert_called_with("Chat with VLM is not initialized. Use CHAT_INIT first.")

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

        # Simulate that files exist. The first call is for history.json, the second for the image.
        mock_path_exists.side_effect = [True, True]

        # Mock conversation history
        history_data = [
            {"role": "user", "parts": [{"type": "text", "data": "Restored prompt"}]},
            {"role": "assistant", "parts": [{"type": "image", "path": "assets/image_0.jpg"}]}
        ]
        mock_file_open.return_value.read.return_value = json.dumps(history_data)

        # Mock LLM factory and conversation object
        mock_conversation = MagicMock()
        # This mock needs to handle a dynamic call to add_image_message
        if not hasattr(mock_conversation, 'add_image_message'):
            mock_conversation.add_image_message = MagicMock()

        mock_factory_instance = MagicMock()
        mock_factory_instance.get_conversation.return_value = mock_conversation
        mock_llm_factories.__getitem__.return_value.return_value = mock_factory_instance
        
        # Mock image
        mock_image = MagicMock()
        mock_image_open.return_value = mock_image

        await self.chat_manager.restore_session(chat_id)

        # Verify that the history file was read
        mock_file_open.assert_called_with(json_path, 'r', encoding='utf-8')
        
        # Verify that the conversation was reconstructed
        self.assertEqual(self.mock_mission_context.conversation, mock_conversation)
        
        # Check calls to conversation object
        calls = [
            call.begin_transaction(Role.USER),
            call.add_text_message("Restored prompt"),
            call.end_transaction(),
            call.begin_transaction(Role.ASSISTANT),
            call.add_image_message(mock_image),
            call.end_transaction()
        ]
        mock_conversation.assert_has_calls(calls, any_order=False)
        mock_image_open.assert_called_once_with(image_path)


    async def test_reset_session(self):
        """Test that the session is correctly reset."""
        self.mock_mission_context.conversation = MagicMock()
        await self.chat_manager.reset_session()
        self.assertIsNone(self.mock_mission_context.conversation)

if __name__ == '__main__':
    unittest.main()
