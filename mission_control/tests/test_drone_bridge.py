
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import json
import errno
import asyncio

from mission_control.bridges.drone_bridge import DroneBridge

class TestDroneBridge(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_config = MagicMock()
        self.mock_config.host = 'localhost'
        self.mock_config.port = 8765
        self.mock_config.max_ws_mb = 10
        self.mock_config.upload_dir = '/fake/uploads'
        self.mock_config.telemetry_dir = '/fake/telemetry'
        
        self.mock_mission_context = MagicMock()
        self.drone_bridge = DroneBridge(self.mock_config, self.mock_mission_context)

    @patch('websockets.serve', new_callable=AsyncMock)
    async def test_start_server_success(self, mock_serve):
        """Test successful server start."""
        mock_server_instance = AsyncMock()
        mock_server_instance.close = MagicMock() # Make close synchronous
        mock_serve.return_value = mock_server_instance
        
        await self.drone_bridge.start()
        
        mock_serve.assert_called_once_with(
            self.drone_bridge.handler, 'localhost', 8765, max_size=10 * 1024 * 1024
        )
        self.assertEqual(self.drone_bridge.server, mock_server_instance)

    @patch('websockets.serve', new_callable=AsyncMock)
    async def test_start_server_address_in_use(self, mock_serve):
        """Test server start failure due to address in use."""
        mock_serve.side_effect = OSError(errno.EADDRINUSE, "Address already in use")
        
        with self.assertRaises(OSError):
            await self.drone_bridge.start()
        
        self.assertIsNone(self.drone_bridge.server)

    async def test_stop_server_success(self):
        """Test successful server and client stop."""
        mock_server = AsyncMock()
        mock_server.close = MagicMock() # Make the close method synchronous
        mock_client = AsyncMock()
        
        self.drone_bridge.server = mock_server
        self.drone_bridge.client = mock_client
        
        await self.drone_bridge.stop()
        
        mock_server.close.assert_called_once()
        mock_server.wait_closed.assert_called_once()
        mock_client.close.assert_called_once()
        self.assertIsNone(self.drone_bridge.client)

    async def test_handler_reject_second_connection(self):
        """Test handler rejecting a second connection."""
        self.drone_bridge.client = MagicMock() # A client is already connected
        
        mock_ws = AsyncMock()
        mock_ws.remote_address = ('127.0.0.1', 54321)
        
        await self.drone_bridge.handler(mock_ws)
        
        mock_ws.send.assert_called_once_with("[SERVER] ERROR: System busy. Another drone is already connected.")
        self.assertNotEqual(self.drone_bridge.client, mock_ws)

    async def test_send_message_success(self):
        """Test sending a simple message to a connected drone."""
        mock_client = AsyncMock()
        self.drone_bridge.client = mock_client
        
        result = await self.drone_bridge.send_message("TEST_CMD")
        
        self.assertTrue(result)
        mock_client.send.assert_called_once_with("TEST_CMD")

    async def test_send_message_no_client(self):
        """Test that sending a message fails if no client is connected."""
        result = await self.drone_bridge.send_message("TEST_CMD")
        self.assertFalse(result)

    async def test_send_command_found(self):
        """Test sending a 'FOUND' command."""
        mock_client = AsyncMock()
        self.drone_bridge.client = mock_client
        
        result = await self.drone_bridge.send_command(found=True)
        
        self.assertTrue(result)
        sent_data = json.loads(mock_client.send.call_args[0][0])
        self.assertEqual(sent_data['type'], 'COMMAND')
        self.assertEqual(sent_data['action'], 'FOUND')

    async def test_send_command_move(self):
        """Test sending a 'MOVE' command."""
        mock_client = AsyncMock()
        self.drone_bridge.client = mock_client
        
        result = await self.drone_bridge.send_command(move=(1.0, 2.5, -3.0))
        
        self.assertTrue(result)
        sent_data = json.loads(mock_client.send.call_args[0][0])
        self.assertEqual(sent_data['type'], 'COMMAND')
        self.assertEqual(sent_data['move'], [1.0, 2.5, -3.0])

    async def test_send_command_no_content(self):
        """Test that send_command fails if no action is specified."""
        mock_client = AsyncMock()
        self.drone_bridge.client = mock_client
        
        result = await self.drone_bridge.send_command()
        
        self.assertFalse(result)
        mock_client.send.assert_not_called()

if __name__ == '__main__':
    unittest.main()
