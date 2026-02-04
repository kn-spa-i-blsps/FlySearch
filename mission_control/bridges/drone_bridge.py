import base64
import errno
import json
import os
from datetime import datetime
from typing import Dict, Any

import websockets
from websockets.frames import CloseCode

from mission_control.core.config import Config
from mission_control.core.mission_context import MissionContext
from mission_control.utils.image_processing import crop_img_square


class DroneBridge:
    """ Handles WebSocket communication between the server and the drone. """
    def __init__(self, config : Config, mission_context: MissionContext):
        self.client = None
        self.config = config
        self.mission_context = mission_context
        self.server = None
        self.collision_warning_str = "Your move would cause a collision. Make other move."

    async def start(self):
        """ Starts WebSocket server in the background.

            Raises error if occurs.
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

    async def send_message(self, cmd):
        """ Transmits a message to the connected drone via WebSocket.

        :return:
            True if successful, False otherwise.
        """
        ws = self.client
        if ws is None:
            print("No drone connected!")
            return False
        try:
            await ws.send(cmd.upper())
            print(f"[WS] {cmd.upper()} sent to the drone.")
            return True
        except Exception as e:
            print(f"[WS] send failed: {e}")
            return False

    async def send_command(self, *, found: bool = False, move=None) -> bool:
        """ Send command to the drone.

          - found=True  ->  {"type":"COMMAND","action":"FOUND", ...}
          - move=(x,y,z)->  {"type":"COMMAND","move":[x,y,z], ...}
        :return:
            True if successful, False otherwise.
        """
        ws = self.client
        if ws is None:
            print("[WS] No drone connected - command NOT sent.")
            return False

        payload : Dict[str, Any] = {
            "type": "COMMAND",
            "ts": datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
        }

        if found:
            payload["action"] = "FOUND"
        elif move is not None:
            try:
                x, y, z = float(move[0]), float(move[1]), float(move[2])
            except Exception as e:
                print(f"[WS] Invalid move triple {move}: {e}")
                return False
            payload["move"] = [x, y, z]
        else:
            print("[WS] No command content (neither 'found' nor 'move'). Not sending.")
            return False

        # Sending payload as a JSON.
        try:
            await ws.send(json.dumps(payload))
            print("[WS] COMMAND sent to the drone.")
            return True
        except Exception as e:
            print("[WS] COMMAND send failed:", e)
            return False

    async def handler(self, ws):
        """ Handle received messages from the drone. """
        peer = ws.remote_address # IP address and port of the connected drone.
        if self.client is not None:
            print(f"[WS] REJECTED connection from {peer} (System busy)")
            await ws.send("[SERVER] ERROR: System busy. Another drone is already connected.")
            return

        self.client = ws
        print(f"[WS] connected: {peer}")

        try:
            # Wait for incoming messages.
            async for message in ws:
                # binary photo - 'photo' command sends photo from rpi that way (idk why, probably will change)
                if isinstance(message, (bytes, bytearray)):
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    file_base = f"img_{ts}"
                    file_name = f"{file_base}.jpg"
                    path = os.path.join(self.config.upload_dir, file_name)
                    with open(path, "wb") as f:
                        f.write(message)
                    print(f"[WS] saved binary -> {path}")
                    await ws.send(f"[SERVER] SAVED {path}")
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

                # TODO: is it useful?
                if obj.get("type") == "ACK" and obj.get("of") == "COMMAND":
                    print(f"[ACK ← RPi] COMMAND seq={obj.get('seq')} ok={obj.get('ok')} err={obj.get('error')}")
                    continue

                # Get the telemetry from the drone. Data is NOT saved in cache, and so not used when talking with vlm.
                if obj.get("type") == "TELEMETRY":
                    data = obj.get("data", {})
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    file_base = f"telemetry_{ts}"
                    file_name = f"{file_base}.json"
                    path = os.path.join(self.config.telemetry_dir, file_name)

                    payload = {
                        "received_at": ts,
                        "associated_photo": None,
                        "data": data
                    }

                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False, indent=2)
                    print(f"[WS] saved telemetry(JSON) -> {path}")
                    await ws.send(f"[SERVER] SAVED {path}")
                    continue

                # Get the data (telemetry and photo) from the drone.
                if obj.get("type") == "PHOTO_WITH_TELEMETRY":

                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

                    photo_base64 = obj.get("photo")
                    telemetry_data = obj.get("telemetry", {})

                    # Photo
                    if not photo_base64:
                        print("[WS] 'PHOTO_WITH_TELEMETRY' received but 'photo' field is missing.")
                        await ws.send("[SERVER] ERROR: Photo data missing in combined message.")
                        continue

                    try:
                        photo_data = base64.b64decode(photo_base64)
                    except TypeError as e:
                        print(f"[WS] Failed to decode Base64 photo data: {e}")
                        await ws.send("[SERVER] ERROR: Invalid photo data encoding.")
                        continue

                    img_file_base = f"img_{ts}"
                    img_file_name = f"{img_file_base}.jpg"
                    img_path = os.path.join(self.config.upload_dir, img_file_name)

                    try:
                        img_cropped, side = crop_img_square(photo_data)

                        img_cropped.save(img_path, format="JPEG", quality=90)
                        print(f"[WS] saved *square* photo -> {img_path} ({side}x{side})")
                    except Exception as e:
                        print(f"[WS] square crop failed, saving raw photo: {e}")
                        with open(img_path, "wb") as f:
                            f.write(photo_data)
                            print(f"[WS] saved photo (raw) -> {img_path}")

                    # Telemetry
                    tel_file_base = f"telemetry_{ts}"
                    tel_file_name = f"{tel_file_base}.json"
                    tel_path = os.path.join(self.config.telemetry_dir, tel_file_name)

                    # We are caching paths for easier access after when sending to VLM.
                    self.mission_context.last_photo_path_cache = img_path
                    self.mission_context.last_telemetry_path_cache = tel_path

                    payload = {
                        "received_at": ts,
                        "associated_photo": img_file_name,
                        "data": telemetry_data
                    }

                    with open(tel_path, "w", encoding="utf-8") as f:
                        try:
                            json.dump(payload, f, ensure_ascii=False, indent=2)
                            print(f"[WS] saved telemetry -> {tel_path}")
                        except Exception as e:
                            print(f"[WS] error saving telemetry: {e}")

                    await ws.send("[SERVER] Photo and telemetry received.")
                    continue

                continue

        except websockets.ConnectionClosed:
            print(f"[WS] disconnected: {peer}.")

        except Exception as e:
            print(f"[WS] error: {e}.")

        finally:
            # Always reset the client.
            self.client = None