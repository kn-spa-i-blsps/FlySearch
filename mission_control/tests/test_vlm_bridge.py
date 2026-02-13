
import unittest
from unittest.mock import Mock, patch, MagicMock

from mission_control.bridges.vlm_bridge import VLMBridge, ParsingError


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

    async def test_send_to_vlm_no_conversation(self):
        # Arrange
        self.mission_context.conversation = None

        # Act
        await self.bridge.send_to_vlm()

        # Assert
        # Check that no processing happens
        self.assertIsNone(self.mission_context.parsed_response)

    @patch('mission_control.bridges.vlm_bridge.parse_xml_response')
    @patch('mission_control.bridges.vlm_bridge.add_grid')
    @patch('mission_control.bridges.vlm_bridge.parse_telemetry')
    async def test_send_to_vlm_parsing_error(self, mock_parse_telemetry, mock_add_grid, mock_parse_xml_response):
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

        # Act
        await self.bridge.send_to_vlm()

        # Assert
        # Check that parsed_response is not set
        self.assertIsNone(self.mission_context.parsed_response)

    @patch('mission_control.bridges.vlm_bridge.parse_telemetry')
    @patch('mission_control.bridges.vlm_bridge.add_grid')
    async def test_send_to_vlm_file_not_found(self, mock_add_grid, mock_parse_telemetry):
        # Arrange
        self.mission_context.conversation = MagicMock()
        self.mission_context.last_photo_path_cache = 'dummy_photo.jpg'
        self.mission_context.last_telemetry_path_cache = 'dummy_telemetry.json'
        mock_add_grid.side_effect = FileNotFoundError
        mock_parse_telemetry.return_value = ('telemetry_text', 100)

        # Act
        await self.bridge.send_to_vlm()

        # Assert
        # Check that parsed_response is not set
        self.assertIsNone(self.mission_context.parsed_response)

if __name__ == '__main__':
    unittest.main()
