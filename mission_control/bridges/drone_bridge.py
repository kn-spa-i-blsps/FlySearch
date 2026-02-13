import base64
import errno
import json
import os
from datetime import datetime
from typing import Dict, Any

import websockets
from websockets.frames import CloseCode

from mission_control.core.config import Config
from mission_control.core.exceptions import NoDroneConnectedError, DroneCommandFailedError, DroneError, \
    DroneConnectionLostError, DroneAlreadyConnectedError, DroneInvalidDataError
from mission_control.core.mission_context import MissionContext
from mission_control.utils.image_processing import crop_img_square


class DroneBridge:
    """ Handles WebSocket communication between the server and the drone. """

    def __init__(self, config : Config, mission_context: MissionContext):
        self.client = None                      # connected drone.
        self.config = config                    # Configuration variables - dirs, ports, hosts...
        self.mission_context = mission_context  # Place to put where the photo or telemetry is saved.
        self.server = None                      # WebSocket server.

    ''' ---------- WEBSOCKET LOGIC ---------- '''
    async def start(self):
        """ Starts WebSocket server in the background.

            Raises:
                OSError: If the server cannot be started (e.g., port in use).
        """

        print(f"[WS] Starting server on {self.config.host}:{self.config.port}...")

        # Open WebSocket server.
        try:
            self.server = await websockets.serve(
                self.handler,
                self.config.host,
                self.config.port,
                max_size=self.config.max_ws_mb * 1024 * 1024
            )
            print("[WS] Server is running and listening for connections.")

        except OSError as e:
            print(f"[CRITICAL ERROR] Could not start WebSocket server on port {self.config.port}.")
            if e.errno == errno.EADDRINUSE:
                print("REASON: Port is already in use!")
                print("HINT: Check if another instance is running or wait a moment.")
            else:
                print(f"REASON: {e.strerror} (Errno: {e.errno})")

            self.server = None

            # Raise the error up the stream.
            raise e

    async def stop(self):
        """ Closes the server and disconnects connected drone. """

        print("[WS] Stopping server...")

        # Closing the server.
        if self.server:
            try:
                self.server.close()
                await self.server.wait_closed()
            except Exception as e:
                # Only log - we want to disconnect the drone.
                print(f"[WS] Warning: Error checking server close: {e}")

        # Disconnecting the drone.
        if self.client:
            try:
                await self.client.close(code=CloseCode.GOING_AWAY, reason="Server shutdown")
            except websockets.ConnectionClosed:
                # Drone might be already disconnected.
                pass
            except Exception as e:
                print(f"[WS] Warning: Error closing client connection: {e}")
            finally:
                self.client = None

        print("[WS] Server stopped.")

    async def handler(self, ws):
        """ Handle received messages from the drone. """

        peer = ws.remote_address # IP address and port of the connected drone.
        if self.client is not None:
            print(f"[WS] REJECTED connection from {peer} (System busy)")
            await ws.send("[SERVER] ERROR: System busy. Another drone is already connected.")
            raise DroneAlreadyConnectedError("Another drone is already connected.")

        self.client = ws
        print(f"[WS] connected: {peer}")

        try:
            # Wait for incoming messages.
            async for message in ws:
                # All _handle_* methods will save incoming messages in proper places.
                # binary photo - 'photo' command sends photo from rpi that way (idk why, probably will change)
                if isinstance(message, (bytes, bytearray)):
                    await self._handle_binary_photo(ws, message)
                    continue

                # If the message is not a photo, try to decode it as JSON.
                # Clean whitespace prefixes/suffixes.
                text = message.strip()
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    print(f"[WS] Ignored message (not JSON nor binary): {text}")
                    continue

                if not isinstance(obj, dict):
                    print(f"[WS] Ignored non-dict JSON: {obj}")
                    continue

                match obj:
                    case {"type": "ACK", "of": "COMMAND", "seq": seq, "ok": ok, "error": err}:
                        print(f"[ACK ← RPi] COMMAND seq={seq} "
                              f"ok={ok} err={err}")

                    case {"type": "TELEMETRY", "data": data}:
                        await self._handle_telemetry(data)

                    case {"type": "PHOTO_WITH_TELEMETRY", "photo": photo, "telemetry": telemetry}:
                        await self._handle_telemetry_photo(ws, photo, telemetry)

                    case _:
                        print(f"[WS] message not matching any case.")

        except websockets.ConnectionClosed as e:
            print(f"[WS] disconnected: {peer}.")
            raise DroneConnectionLostError(f"Connection with drone at {peer} lost.") from e

        except Exception as e:
            print(f"[WS] error: {e}.")
            raise DroneError(f"Error during communication with drone: {e}") from e

        finally:
            print("[WS] Client set to None - handler ended.")
            # Always reset the client.
            self.client = None

    ''' ---------- AVAILABLE COMMANDS ---------- '''
    async def send_message(self, cmd):
        """ Transmits a message to the connected drone via WebSocket.

        :raises:
            NoDroneConnectedError: If no drone is connected.
            DroneCommandFailedError: If the message could not be sent.
        """
        if self.client is None:
            raise NoDroneConnectedError("No drone is connected.")

        try:
            await self.client.send(cmd.upper())
            print(f"[WS] {cmd.upper()} sent to the drone.")
        except Exception as e:
            print(f"[WS] send failed: {e}")
            raise DroneCommandFailedError(f"Failed to send message '{cmd.upper()}'") from e

    async def send_command(self, *, found: bool = False, move=None):
        """ Send command to the drone.

          - found=True  ->  {"type":"COMMAND","action":"FOUND", ...}
          - move=(x,y,z)->  {"type":"COMMAND","move":[x,y,z], ...}
        :raises:
            NoDroneConnectedError: If no drone is connected.
            DroneCommandFailedError: If the command could not be sent.
            ValueError: If the move parameters are invalid or no command content is provided.
        """
        if self.client is None:
            raise NoDroneConnectedError("No drone is connected.")

        payload: Dict[str, Any] = {
            "type": "COMMAND",
            "ts": datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
        }

        if found:
            payload["action"] = "FOUND"
        elif move is not None:
            try:
                x, y, z = map(float, move)
            except (ValueError, IndexError, TypeError) as e:
                print(f"[WS] Invalid move triple {move}: {e}")
                raise ValueError(f"Invalid move parameters: {move}") from e
            payload["move"] = [x, y, z]
        else:
            raise ValueError("No command content provided (neither 'found' nor 'move').")

        # Sending payload as a JSON.
        try:
            await self.client.send(json.dumps(payload))
            print("[WS] COMMAND sent to the drone.")
        except Exception as e:
            print("[WS] COMMAND send failed:", e)
            raise DroneCommandFailedError("Failed to send COMMAND to the drone") from e

    # SAVING PHOTOS/JSON IS BLOCKING - WITH MULTIPLE DRONES OR BIG DATA COULD BE BAD
    # may need change into run_in_executor

    ''' ---------- HELPER METHODS ----------'''
    async def _handle_binary_photo(self, ws, message):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_base = f"img_{ts}"
        file_name = f"{file_base}.jpg"
        path = os.path.join(self.config.upload_dir, file_name)
        with open(path, "wb") as f:
            f.write(message)
        print(f"[WS] saved binary -> {path}")
        await ws.send(f"[SERVER] SAVED {path}")

    async def _handle_telemetry(self, data, photo_name=None):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_base = f"telemetry_{ts}"
        file_name = f"{file_base}.json"
        path = os.path.join(self.config.telemetry_dir, file_name)

        payload = {
            "received_at": ts,
            "associated_photo": photo_name,
            "data": data
        }

        with open(path, "w", encoding="utf-8") as f:
            try:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                print(f"[WS] saved telemetry -> {path}")
            except Exception as e:
                print(f"[WS] error saving telemetry: {e}")

        self.mission_context.last_telemetry_path_cache = path

    async def _handle_telemetry_photo(self, ws, photo_base64, telemetry):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Photo
        if not photo_base64:
            raise DroneInvalidDataError("Received 'PHOTO_WITH_TELEMETRY' but 'photo' field is missing.")

        try:
            photo_data = base64.b64decode(photo_base64)
        except (TypeError, ValueError) as e:
            raise DroneInvalidDataError(f"Failed to decode Base64 photo data: {e}") from e

        img_file_base = f"img_{ts}"
        img_file_name = f"{img_file_base}.jpg"
        img_path = os.path.join(self.config.upload_dir, img_file_name)

        # Crop the image to be square (as in original paper).
        try:
            img_cropped, side = crop_img_square(photo_data)

            img_cropped.save(img_path, format="JPEG", quality=90)
            print(f"[WS] saved *square* photo -> {img_path} ({side}x{side})")
        except Exception as e:
            print(f"[WS] square crop failed, saving raw photo: {e}")
            with open(img_path, "wb") as f:
                f.write(photo_data)
                print(f"[WS] saved photo (raw) -> {img_path}")

        # We are caching paths for easier access after, when sending to VLM.
        self.mission_context.last_photo_path_cache = img_path

        # Telemetry
        await self._handle_telemetry(telemetry, img_file_name)

        await ws.send("[SERVER] Photo and telemetry received.")
