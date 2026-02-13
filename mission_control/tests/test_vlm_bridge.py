
import unittest
from unittest.mock import Mock, patch, MagicMock, call

from mission_control.bridges.vlm_bridge import VLMBridge
from mission_control.core.exceptions import VLMPreconditionsNotMetError, VLMParseError, VLMConnectionError
from mission_control.utils.parsers import ParsingError


class TestVLMBridge(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = Mock()
        self.mission_context = Mock()
        self.mission_context.parsed_response = None
        self.bridge = VLMBridge(self.config, self.mission_context)

    @patch('mission_control.bridges.vlm_bridge.parse_xml_response')
    @patch('mission_control.bridges.vlm_bridge.add_grid')
    @patch('mission_control.bridges.vlm_bridge.parse_telemetry')
    async def test_send_to_vlm_success(self, mock_parse_telemetry, mock_add_grid, mock_parse_xml_response):
        # Arrange
        self.mission_context.conversation = MagicMock()
        self.mission_context.last_photo_path_cache = 'dummy_photo.jpg'
        self.mission_context.last_telemetry_path_cache = 'dummy_telemetry.json'

        mock_parse_telemetry.return_value = ('telemetry_text', 100)
        mock_add_grid.return_value = 'gridded_image'
        
        mock_response = Mock()
        mock_response.text = '<response><move>forward</move></response>'
        self.mission_context.conversation.get_latest_message.return_value = mock_response
        
        mock_parse_xml_response.return_value = {'move': 'forward'}

        # Act
        await self.bridge.send_to_vlm()

        # Assert
        self.mission_context.conversation.add_image_message.assert_called_with('gridded_image')
        self.mission_context.conversation.add_text_message.assert_called_with('telemetry_text')
        self.mission_context.conversation.commit_transaction.assert_called_with(send_to_vlm=True)
        mock_parse_xml_response.assert_called_with('<response><move>forward</move></response>')
        self.assertEqual(self.mission_context.parsed_response, {'move': 'forward'})

    @patch('mission_control.bridges.vlm_bridge.parse_xml_response')
    @patch('mission_control.bridges.vlm_bridge.add_grid')
    @patch('mission_control.bridges.vlm_bridge.parse_telemetry')
    async def test_send_to_vlm_with_warning_flag(self, mock_parse_telemetry, mock_add_grid, mock_parse_xml_response):
        # Arrange
        self.mission_context.conversation = MagicMock()
        self.mission_context.last_photo_path_cache = 'dummy_photo.jpg'
        self.mission_context.last_telemetry_path_cache = 'dummy_telemetry.json'

        mock_parse_telemetry.return_value = ('telemetry_text', 100)
        mock_add_grid.return_value = 'gridded_image'
        
        mock_response = Mock()
        mock_response.text = '<response><move>left</move></response>'
        self.mission_context.conversation.get_latest_message.return_value = mock_response
        
        mock_parse_xml_response.return_value = {'move': 'left'}
        
        # Act
        await self.bridge.send_to_vlm(is_warning=True)

        # Assert
        expected_calls = [
            call(self.bridge.collision_warning_str),
            call('telemetry_text')
        ]
        self.mission_context.conversation.add_text_message.assert_has_calls(expected_calls)
        self.mission_context.conversation.add_image_message.assert_called_with('gridded_image')
        self.assertEqual(self.mission_context.parsed_response, {'move': 'left'})

    async def test_send_to_vlm_no_conversation_raises_error(self):
        # Arrange
        self.mission_context.conversation = None
        self.mission_context.last_photo_path_cache = 'dummy_photo.jpg'
        self.mission_context.last_telemetry_path_cache = 'dummy_telemetry.json'

        # Act & Assert
        with self.assertRaises(VLMPreconditionsNotMetError):
            await self.bridge.send_to_vlm()

    async def test_send_to_vlm_no_data_raises_error(self):
        # Arrange
        self.mission_context.conversation = MagicMock()
        self.mission_context.last_photo_path_cache = None
        self.mission_context.last_telemetry_path_cache = None

        # Act & Assert
        with self.assertRaises(VLMPreconditionsNotMetError):
            await self.bridge.send_to_vlm()

    @patch('mission_control.bridges.vlm_bridge.parse_xml_response')
    @patch('mission_control.bridges.vlm_bridge.add_grid')
    @patch('mission_control.bridges.vlm_bridge.parse_telemetry')
    async def test_send_to_vlm_parsing_error_raises_error(self, mock_parse_telemetry, mock_add_grid, mock_parse_xml_response):
        # Arrange
        self.mission_context.conversation = MagicMock()
        self.mission_context.last_photo_path_cache = 'dummy_photo.jpg'
        self.mission_context.last_telemetry_path_cache = 'dummy_telemetry.json'
        
        mock_parse_telemetry.return_value = ('telemetry_text', 100)
        mock_add_grid.return_value = 'gridded_image'
        
        mock_response = Mock()
        mock_response.text = 'invalid_xml'
        self.mission_context.conversation.get_latest_message.return_value = mock_response
        
        mock_parse_xml_response.side_effect = ParsingError("Invalid XML")

        # Act & Assert
        with self.assertRaises(VLMParseError):
            await self.bridge.send_to_vlm()

    @patch('mission_control.bridges.vlm_bridge.parse_telemetry')
    async def test_send_to_vlm_telemetry_file_not_found_raises_error(self, mock_parse_telemetry):
        # Arrange
        self.mission_context.conversation = MagicMock()
        self.mission_context.last_photo_path_cache = 'dummy_photo.jpg'
        self.mission_context.last_telemetry_path_cache = 'dummy_telemetry.json'
        mock_parse_telemetry.side_effect = FileNotFoundError

        # Act & Assert
        with self.assertRaises(FileNotFoundError):
            await self.bridge.send_to_vlm()

    @patch('mission_control.bridges.vlm_bridge.add_grid')
    @patch('mission_control.bridges.vlm_bridge.parse_telemetry')
    async def test_send_to_vlm_photo_file_not_found_raises_error(self, mock_parse_telemetry, mock_add_grid):
        # Arrange
        self.mission_context.conversation = MagicMock()
        self.mission_context.last_photo_path_cache = 'dummy_photo.jpg'
        self.mission_context.last_telemetry_path_cache = 'dummy_telemetry.json'
        mock_parse_telemetry.return_value = ('telemetry_text', 100)
        mock_add_grid.side_effect = FileNotFoundError

        # Act & Assert
        with self.assertRaises(FileNotFoundError):
            await self.bridge.send_to_vlm()

    @patch('mission_control.bridges.vlm_bridge.add_grid')
    @patch('mission_control.bridges.vlm_bridge.parse_telemetry')
    async def test_send_to_vlm_connection_error_raises_error(self, mock_parse_telemetry, mock_add_grid):
        # Arrange
        self.mission_context.conversation = MagicMock()
        self.mission_context.last_photo_path_cache = 'dummy_photo.jpg'
        self.mission_context.last_telemetry_path_cache = 'dummy_telemetry.json'
        
        mock_parse_telemetry.return_value = ('telemetry_text', 100)
        mock_add_grid.return_value = 'gridded_image'
        
        self.mission_context.conversation.commit_transaction.side_effect = Exception("Connection failed")

        # Act & Assert
        with self.assertRaises(VLMConnectionError):
            await self.bridge.send_to_vlm()


if __name__ == '__main__':
    unittest.main()
