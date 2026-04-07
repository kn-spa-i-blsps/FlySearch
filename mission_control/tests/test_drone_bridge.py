
import unittest
from unittest.mock import MagicMock, patch, AsyncMock, mock_open
import json
import errno
import base64

from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from mission_control.done_comm import DroneBridge
from mission_control.core.exceptions import NoDroneConnectedError, DroneCommandFailedError, DroneInvalidDataError, \
    DroneAlreadyConnectedError


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

    async def test_handler_successful_connection(self):
        """Test that a new client connection is handled correctly."""
        mock_ws = AsyncMock()
        mock_ws.remote_address = ('127.0.0.1', 12345)
        # Make the async for loop not yield anything
        mock_ws.__aiter__.return_value = iter([])

        await self.drone_bridge.handler(mock_ws)
        
        # Check that the client was set and then reset
        self.assertIsNone(self.drone_bridge.client)

    async def test_handler_reject_second_connection(self):
        """Test handler rejecting a second connection and raising DroneAlreadyConnectedError."""
        self.drone_bridge.client = MagicMock()  # A client is already connected
        mock_ws = AsyncMock()
        mock_ws.remote_address = ('127.0.0.1', 54321)

        with self.assertRaises(DroneAlreadyConnectedError):
            await self.drone_bridge.handler(mock_ws)

        mock_ws.send.assert_called_once_with("[SERVER] ERROR: System busy. Another drone is already connected.")
        self.assertNotEqual(self.drone_bridge.client, mock_ws)

    @patch('mission_control.bridges.drone_bridge.DroneBridge._handle_binary_photo', new_callable=AsyncMock)
    @patch('mission_control.bridges.drone_bridge.DroneBridge._handle_telemetry', new_callable=AsyncMock)
    @patch('mission_control.bridges.drone_bridge.DroneBridge._handle_telemetry_photo', new_callable=AsyncMock)
    async def test_handler_routes_messages(self, mock_handle_telemetry_photo, mock_handle_telemetry, mock_handle_binary_photo):
        """Test that the handler correctly routes different message types."""
        mock_ws = AsyncMock()
        mock_ws.remote_address = ('127.0.0.1', 12345)
        
        messages = [
            b'binary_image_data',
            json.dumps({"type": "TELEMETRY", "data": {"alt": 10}}),
            json.dumps({"type": "PHOTO_WITH_TELEMETRY", "photo": "base64...", "telemetry": {}}),
            "this is not json",
            json.dumps(["this is not a dict"])
        ]
        mock_ws.__aiter__.return_value = iter(messages)

        await self.drone_bridge.handler(mock_ws)

        mock_handle_binary_photo.assert_called_once_with(mock_ws, b'binary_image_data')
        mock_handle_telemetry.assert_called_once_with({"alt": 10})
        mock_handle_telemetry_photo.assert_called_once_with(mock_ws, "base64...", {})

    @patch("builtins.print")
    async def test_handler_graceful_disconnect_does_not_raise(self, mock_print):
        """Test graceful close is logged without raising an exception from handler."""
        close = Close(1001, "RPi shutdown (Ctrl+C)")
        closed_ok = ConnectionClosedOK(close, close, True)

        class ClosingWS:
            remote_address = ("127.0.0.1", 12345)

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise closed_ok

        await self.drone_bridge.handler(ClosingWS())

        printed = "\n".join(" ".join(map(str, c.args)) for c in mock_print.call_args_list)
        self.assertIn("[WS] disconnected gracefully:", printed)
        self.assertIn("code=1001", printed)
        self.assertIsNone(self.drone_bridge.client)

    @patch("builtins.print")
    async def test_handler_broken_disconnect_does_not_raise(self, mock_print):
        """Test abnormal close is logged without raising an exception from handler."""
        closed_error = ConnectionClosedError(None, None, None)

        class BrokenWS:
            remote_address = ("127.0.0.1", 12345)

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise closed_error

        await self.drone_bridge.handler(BrokenWS())

        printed = "\n".join(" ".join(map(str, c.args)) for c in mock_print.call_args_list)
        self.assertIn("[WS] connection broken:", printed)
        self.assertIn("code=1006", printed)
        self.assertIsNone(self.drone_bridge.client)

    @patch("builtins.print")
    async def test_handler_ack_without_error_field_is_accepted(self, mock_print):
        """Test ACK messages without an 'error' field are parsed and logged."""
        mock_ws = AsyncMock()
        mock_ws.remote_address = ('127.0.0.1', 12345)
        mock_ws.__aiter__.return_value = iter([
            json.dumps({"type": "ACK", "of": "COMMAND", "seq": 7, "ok": True, "executed": True})
        ])

        await self.drone_bridge.handler(mock_ws)

        printed = "\n".join(" ".join(map(str, c.args)) for c in mock_print.call_args_list)
        self.assertIn("[ACK ← RPi] COMMAND seq=7 ok=True", printed)
        self.assertNotIn("[WS] message not matching any case.", printed)

    @patch('os.path.join')
    @patch('builtins.open', new_callable=mock_open)
    @patch('mission_control.bridges.drone_bridge.datetime')
    async def test_handle_binary_photo_saves_file(self, mock_datetime, mock_file_open, mock_path_join):
        """Test that _handle_binary_photo saves the binary data to a file."""
        mock_now = MagicMock()
        mock_now.strftime.return_value = "20230101_120000"
        mock_datetime.now.return_value = mock_now
        
        fake_path = '/fake/uploads/img_20230101_120000.jpg'
        mock_path_join.return_value = fake_path

        mock_ws = AsyncMock()
        binary_data = b'test_image_data'
        
        await self.drone_bridge._handle_binary_photo(mock_ws, binary_data)

        mock_path_join.assert_called_once_with(self.mock_config.upload_dir, 'img_20230101_120000.jpg')
        mock_file_open.assert_called_once_with(fake_path, 'wb')
        mock_file_open().write.assert_called_once_with(binary_data)
        mock_ws.send.assert_called_once_with(f'[SERVER] SAVED {fake_path}')

    @patch('os.path.join')
    @patch('builtins.open', new_callable=mock_open)
    @patch('json.dump')
    @patch('mission_control.bridges.drone_bridge.datetime')
    async def test_handle_telemetry_saves_file(self, mock_datetime, mock_json_dump, mock_file_open, mock_path_join):
        """Test that _handle_telemetry saves the telemetry data to a file."""
        mock_now = MagicMock()
        ts = "20230101_120000"
        mock_now.strftime.return_value = ts
        mock_datetime.now.return_value = mock_now
        
        fake_path = f'/fake/telemetry/telemetry_{ts}.json'
        mock_path_join.return_value = fake_path

        telemetry_data = {"alt": 15, "lat": 12.34}
        photo_name = "img_20230101_115900.jpg"
        
        await self.drone_bridge._handle_telemetry(telemetry_data, photo_name)

        mock_path_join.assert_called_once_with(self.mock_config.telemetry_dir, f'telemetry_{ts}.json')
        mock_file_open.assert_called_once_with(fake_path, 'w', encoding='utf-8')
        
        expected_payload = {
            "received_at": ts,
            "associated_photo": photo_name,
            "data": telemetry_data
        }
        mock_json_dump.assert_called_once_with(expected_payload, mock_file_open(), ensure_ascii=False, indent=2)
        
        self.assertEqual(self.mock_mission_context.last_telemetry_path_cache, fake_path)

    @patch('os.path.join')
    @patch('mission_control.bridges.drone_bridge.crop_img_square')
    @patch('mission_control.bridges.drone_bridge.DroneBridge._handle_telemetry', new_callable=AsyncMock)
    @patch('mission_control.bridges.drone_bridge.datetime')
    async def test_handle_telemetry_photo_success(self, mock_datetime, mock_handle_telemetry, mock_crop, mock_path_join):
        """Test successful handling of a photo with telemetry."""
        mock_now = MagicMock()
        ts = "20230101_120000"
        mock_now.strftime.return_value = ts
        mock_datetime.now.return_value = mock_now

        fake_path = f'/fake/uploads/img_{ts}.jpg'
        mock_path_join.return_value = fake_path

        mock_ws = AsyncMock()
        photo_b64 = base64.b64encode(b'fake_image_data').decode('utf-8')
        telemetry_data = {"alt": 20}

        mock_cropped_img = MagicMock()
        mock_crop.return_value = (mock_cropped_img, 128)

        await self.drone_bridge._handle_telemetry_photo(mock_ws, photo_b64, telemetry_data)

        mock_crop.assert_called_once_with(base64.b64decode(photo_b64))
        mock_cropped_img.save.assert_called_once_with(fake_path, format="JPEG", quality=90)
        self.assertEqual(self.mock_mission_context.last_photo_path_cache, fake_path)
        mock_handle_telemetry.assert_called_once_with(telemetry_data, f'img_{ts}.jpg')

    @patch('os.path.join')
    @patch('builtins.open', new_callable=mock_open)
    @patch('mission_control.bridges.drone_bridge.crop_img_square', side_effect=Exception("Crop failed"))
    @patch('mission_control.bridges.drone_bridge.DroneBridge._handle_telemetry', new_callable=AsyncMock)
    @patch('mission_control.bridges.drone_bridge.datetime')
    async def test_handle_telemetry_photo_crop_fails(self, mock_datetime, mock_handle_telemetry, mock_crop, mock_file_open, mock_path_join):
        """Test fallback to saving raw photo when cropping fails."""
        mock_now = MagicMock()
        ts = "20230101_120000"
        mock_now.strftime.return_value = ts
        mock_datetime.now.return_value = mock_now

        fake_path = f'/fake/uploads/img_{ts}.jpg'
        mock_path_join.return_value = fake_path

        mock_ws = AsyncMock()
        raw_photo_data = b'fake_image_data'
        photo_b64 = base64.b64encode(raw_photo_data).decode('utf-8')
        telemetry_data = {"alt": 20}

        await self.drone_bridge._handle_telemetry_photo(mock_ws, photo_b64, telemetry_data)

        mock_file_open.assert_called_once_with(fake_path, 'wb')
        mock_file_open().write.assert_called_once_with(raw_photo_data)
        self.assertEqual(self.mock_mission_context.last_photo_path_cache, fake_path)
        mock_handle_telemetry.assert_called_once_with(telemetry_data, f'img_{ts}.jpg')

    async def test_send_message_success(self):
        """Test sending a simple message to a connected drone."""
        mock_client = AsyncMock()
        self.drone_bridge.client = mock_client
        
        await self.drone_bridge.send_message("TEST_CMD")
        
        mock_client.send.assert_called_once_with("TEST_CMD")

    async def test_send_message_no_client_raises_error(self):
        """Test that send_message raises NoDroneConnectedError if no client is connected."""
        with self.assertRaises(NoDroneConnectedError):
            await self.drone_bridge.send_message("TEST_CMD")

    async def test_send_message_failure_raises_error(self):
        """Test that send_message raises DroneCommandFailedError on send failure."""
        mock_client = AsyncMock()
        mock_client.send.side_effect = Exception("Send failed")
        self.drone_bridge.client = mock_client

        with self.assertRaises(DroneCommandFailedError):
            await self.drone_bridge.send_message("TEST_CMD")

    async def test_send_command_found(self):
        """Test sending a 'FOUND' command."""
        mock_client = AsyncMock()
        self.drone_bridge.client = mock_client
        
        await self.drone_bridge.send_move(found=True)
        
        sent_data = json.loads(mock_client.send.call_args[0][0])
        self.assertEqual(sent_data['type'], 'COMMAND')
        self.assertEqual(sent_data['action'], 'FOUND')

    async def test_send_command_move(self):
        """Test sending a 'MOVE' command."""
        mock_client = AsyncMock()
        self.drone_bridge.client = mock_client
        
        await self.drone_bridge.send_move(move=(1.0, 2.5, -3.0))
        
        sent_data = json.loads(mock_client.send.call_args[0][0])
        self.assertEqual(sent_data['type'], 'COMMAND')
        self.assertEqual(sent_data['move'], [1.0, 2.5, -3.0])

    async def test_send_command_invalid_move_raises_error(self):
        """Test send_command raises ValueError for invalid move parameters."""
        self.drone_bridge.client = AsyncMock()
        
        invalid_moves = [
            (1, 2),          # Too few values
            (1, 2, 3, 4),    # Too many values
            ('a', 'b', 'c'), # Non-numeric values
            None,
        ]
        
        for move in invalid_moves:
            with self.subTest(move=move):
                with self.assertRaises(ValueError):
                    await self.drone_bridge.send_move(move=move)

    async def test_send_command_no_content_raises_error(self):
        """Test that send_command raises ValueError if no action is specified."""
        self.drone_bridge.client = AsyncMock()
        
        with self.assertRaises(ValueError):
            await self.drone_bridge.send_move()

    @patch('mission_control.bridges.drone_bridge.DroneBridge._handle_telemetry', new_callable=AsyncMock)
    async def test_handle_telemetry_photo_missing_photo_skips_frame(self, mock_handle_telemetry):
        """Test _handle_telemetry_photo skips frame if photo data is missing."""
        mock_ws = AsyncMock()
        telemetry_data = {"alt": 42}
        await self.drone_bridge._handle_telemetry_photo(mock_ws, None, telemetry_data)
        mock_handle_telemetry.assert_called_once_with(telemetry_data, None)

    async def test_handle_telemetry_photo_bad_encoding_raises_error(self):
        """Test _handle_telemetry_photo raises DroneInvalidDataError for bad base64 data."""
        mock_ws = AsyncMock()
        with self.assertRaises(DroneInvalidDataError):
            await self.drone_bridge._handle_telemetry_photo(mock_ws, "not-base64", {})

if __name__ == '__main__':
    unittest.main()
