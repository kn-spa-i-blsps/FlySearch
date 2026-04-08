import errno
import json
import traceback

import websockets

from mission_control.core.config import Config
from mission_control.core.events import *
from mission_control.core.exceptions import DroneCommunicationError
from mission_control.core.interfaces import DataStorageHelper
from mission_control.drone_comm.video_helper import VideoHelper
from mission_control.utils.event_bus import EventBus
from mission_control.utils.logger import get_configured_logger

logger = get_configured_logger(__name__)


class WebSocketDroneBridge:
    """ Handles WebSocket communication between the server and the drone. """

    def __init__(self, config: Config, event_bus: EventBus, video_helper: VideoHelper, storage: DataStorageHelper):
        self.connected_clients: Dict[str, Any] = {}  # Active drone connections mapping {drone_id: ws_instance}
        self.disconnected_clients = {}  # Tracks drones that lost connection abnormally
        self.config = config  # System configuration (directories, ports, hosts, etc.)
        self.event_bus = event_bus  # Event bus for the event-driven architecture
        self.server = None  # WebSocket server instance
        self.video_helper = video_helper
        self.storage = storage

        # Subscribe to standard core commands
        self.event_bus.subscribe(GetPhotoAndTelemetryCommand, self.handle_get_photo_telemetry)
        self.event_bus.subscribe(ExecuteMoveCommand, self.send_move)

        # Subscribe to video-related commands
        self.event_bus.subscribe(StartRecordingCommand, self.handle_start_recording)
        self.event_bus.subscribe(StopRecordingCommand, self.handle_stop_recording)
        self.event_bus.subscribe(GetRecordingsListCommand, self.handle_get_recordings)
        self.event_bus.subscribe(PullRecordingsCommand, self.handle_pull_recordings)

    ''' ---------- WEBSOCKET LOGIC ---------- '''

    async def start(self) -> None:
        """ Starts the WebSocket server in the background.

            Raises:
                DroneCommunicationError: If the server cannot be started (e.g., port already in use).
        """
        logger.info("[WS] Starting server on %s:%d", self.config.host, self.config.port)

        try:
            serve_kwargs = {
                "max_size": self.config.max_ws_mb * 1024 * 1024,
                "ping_interval": getattr(self.config, "ws_ping_interval", None),
                "ping_timeout": getattr(self.config, "ws_ping_timeout", None),
            }

            self.server = await websockets.serve(
                self.handler,
                self.config.host,
                self.config.port,
                **serve_kwargs,
            )
            logger.info("[WS] Server is running and listening for connections.")
        except OSError as e:
            self.server = None
            reason = (
                "Port is already in use!"
                if e.errno == errno.EADDRINUSE
                else f"{e.strerror} (Errno: {e.errno})"
            )
            raise DroneCommunicationError(
                f"[WS] Could not start WebSocket server on port {self.config.port}.\n"
                f"REASON: {reason}"
            ) from e

    async def stop(self) -> None:
        """ Closes the server and cleanly disconnects any connected drones. """
        logger.info("[WS] Stopping server...")

        if self.server:
            self.server.close()
            await self.server.wait_closed()

        self.connected_clients.clear()
        self.server = None
        logger.info("[WS] Server stopped.")

    async def handler(self, ws):
        """ Handles the lifecycle of a connected drone. """
        peer = ws.remote_address  # IP address and port of the connected drone.

        # Currently, the system restricts connection to a single drone at a time.
        if self.connected_clients:
            logger.info(f"[WS] Rejected connection from {peer} (System busy)")
            return

        # TODO: Implement a proper handshake protocol where the drone identifies itself with a unique ID.
        drone_id = "drone"

        self.connected_clients[drone_id] = ws

        logger.info(f"[WS] connected: {peer}")

        try:
            async for message in ws:
                # The drone sends binary payloads exclusively for raw photos.
                if isinstance(message, (bytes, bytearray)):
                    await self.storage.save_binary_photo(message)
                    continue

                # For all other messages, attempt to parse them as JSON.
                text = message.strip()
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    logger.warning(f"[WS] Ignored message (not JSON nor binary): {text}")
                    continue

                if not isinstance(obj, dict):
                    logger.warning(f"[WS] Ignored non-dict JSON: {obj}")
                    continue

                # Route the message based on its structure and type.
                match obj:
                    case {"type": "ACK", "of": "COMMAND", "seq": seq, "ok": ok, **ack_rest}:
                        err = ack_rest.get("error")
                        executed = ack_rest.get("executed")
                        logger.debug(
                            f"[ACK ← RPi] COMMAND seq={seq} "
                            f"ok={ok} executed={executed} err={err}"
                        )
                    case {"type": "ACK", "of": "RECORDING", "action": action, "ok": ok, **ack_rest}:
                        self.video_helper.handle_recording_ack(obj)

                    case {"type": "ACK", "of": "RECORDINGS", "action": action, "ok": ok, **ack_rest}:
                        self.video_helper.handle_recordings_ack(obj)

                    case {"type": "RECORDING_FILE_BEGIN", "transfer_id": transfer_id, "name": name, **file_rest}:
                        await self.video_helper.handle_recording_file_begin(
                            transfer_id=str(transfer_id),
                            name=str(name),
                            payload=file_rest,
                        )
                    case {"type": "RECORDING_FILE_CHUNK", "transfer_id": transfer_id, "name": name, "seq": seq,
                          "data": data}:
                        await self.video_helper.handle_recording_file_chunk(
                            transfer_id=str(transfer_id),
                            name=str(name),
                            seq=int(seq),
                            chunk_b64=str(data),
                        )
                    case {"type": "RECORDING_FILE_END", "transfer_id": transfer_id, "name": name, **end_rest}:
                        await self.video_helper.handle_recording_file_end(
                            transfer_id=str(transfer_id),
                            name=str(name),
                            payload=end_rest,
                        )

                    case {"type": "TELEMETRY", "data": data}:
                        await self.storage.save_telemetry(data)

                    case {"type": "PHOTO_WITH_TELEMETRY", "photo": photo, "telemetry": telemetry}:
                        photo_path, telemetry_path = await self.storage.save_photo_and_telemetry(photo, telemetry)
                        await self.event_bus.publish(PhotoWithTelemetryReceived(
                            drone_id=drone_id,
                            photo_path=photo_path,
                            telemetry_path=telemetry_path
                        ))

                    case _:
                        logger.warning(f"[WS] message not matching any case: {obj}")

        except websockets.ConnectionClosedOK as e:
            logger.info(f"[WS] Drone disconnected gracefully: {peer}. {self.video_helper.format_disconnect_reason(e)}")

        except websockets.ConnectionClosedError as e:
            logger.warning(f"[WS] Drone connection broken: {peer}. {self.video_helper.format_disconnect_reason(e)}")

        except websockets.ConnectionClosed as e:
            logger.warning(f"[WS] disconnected: {peer}. {self.video_helper.format_disconnect_reason(e)}")

        except Exception as e:
            logger.error(f"[WS] error: {e}")
            logger.debug(traceback.format_exc())

        finally:
            # Clean up pending futures and file handles upon disconnection
            #TODO: cleanup method
            self.video_helper._clear_waiters(self.video_helper._recording_ack_waiters, "Connection lost before ACK")
            self.video_helper._clear_waiters(self.video_helper._recordings_ack_waiters, "Connection lost before ACK")
            self.video_helper._cleanup_pull_transfers()

            # Remove the drone from the active clients registry
            self.connected_clients.pop(drone_id, None)

    ''' ---------- SUBSCRIBED COMMANDS (CORE) ---------- '''

    async def handle_get_photo_telemetry(self, event: GetPhotoAndTelemetryCommand):
        await self.send_message(event.drone_id, "PHOTO_WITH_TELEMETRY")

    async def send_message(self, drone_id: str, cmd: str) -> None:
        """
        Transmits a raw string message to the connected drone via WebSocket.

        Publishes:
            DroneErrorOccurred: If the drone is not connected or the transmission fails.
        """
        try:
            if drone_id not in self.connected_clients:
                raise DroneCommunicationError(f"Drone {drone_id} is not connected.")
            client = self.connected_clients[drone_id]
            await client.send(cmd)

        except Exception as e:
            error_event = DroneErrorOccurred(
                drone_id=drone_id,
                error_message=f"[WS] Failed to send message to drone {drone_id}: {str(e)}",
                traceback=traceback.format_exc()
            )
            await self.event_bus.publish(error_event)
            logger.error(f"[WS] Failed to send message to drone {drone_id}: {e}")

    async def send_move(self, event: ExecuteMoveCommand):
        """
        Sends a movement command vector to the drone.

        Raises:
            ValueError: If the movement coordinates are invalid or missing.
        """
        drone_id = event.drone_id
        move = event.move

        try:
            if drone_id not in self.connected_clients:
                raise DroneCommunicationError(f"Drone {drone_id} is not connected.")
            client = self.connected_clients[drone_id]

            payload: Dict[str, Any] = {
                "type": "COMMAND",
                "ts": datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
            }

            if move is not None:
                try:
                    x, y, z = map(float, move)
                except (ValueError, IndexError, TypeError) as e:
                    raise ValueError(f"[WS] Invalid move coordinates {move}: {e}") from e
                payload["move"] = [x, y, z]
            else:
                raise ValueError("[WS] No move content provided (neither 'found' nor 'move').")

            await client.send(json.dumps(payload))

        except Exception as e:
            error_event = DroneErrorOccurred(
                drone_id=drone_id,
                error_message=f"[WS] Failed to send move command to drone {drone_id}: {str(e)}",
                traceback=traceback.format_exc()
            )
            await self.event_bus.publish(error_event)
            logger.error(f"[WS] Failed to send move to drone {drone_id}: {e}")

    ''' ---------- SUBSCRIBED COMMANDS (VIDEO HELPER) ---------- '''

    async def handle_start_recording(self, event: StartRecordingCommand):
        drone_id = event.drone_id
        try:
            ws = self.connected_clients.get(drone_id)
            if not ws:
                raise DroneCommunicationError(f"Drone {drone_id} is not connected.")

            ack = await self.video_helper.send_recording_command(ws, "START_RECORDING")
            logger.info(f"[WS] Video recording started on {drone_id}. ACK: {ack}")

        except Exception as e:
            await self.event_bus.publish(
                DroneErrorOccurred(drone_id=drone_id, error_message=f"Start recording failed: {e}", traceback=traceback.format_exc()))
            logger.error(f"[WS] Start recording failed for {drone_id}: {e}")

    async def handle_stop_recording(self, event: StopRecordingCommand):
        drone_id = event.drone_id
        try:
            ws = self.connected_clients.get(drone_id)
            if not ws:
                raise DroneCommunicationError(f"Drone {drone_id} is not connected.")

            ack = await self.video_helper.send_recording_command(ws, "STOP_RECORDING")
            logger.info(f"[WS] Video recording stopped on {drone_id}. ACK: {ack}")

        except Exception as e:
            await self.event_bus.publish(
                DroneErrorOccurred(drone_id=drone_id, error_message=f"Stop recording failed: {e}", traceback=traceback.format_exc()))
            logger.error(f"[WS] Stop recording failed for {drone_id}: {e}")

    async def handle_get_recordings(self, event: GetRecordingsListCommand):
        drone_id = event.drone_id
        try:
            ws = self.connected_clients.get(drone_id)
            if not ws:
                raise DroneCommunicationError(f"Drone {drone_id} is not connected.")

            ack = await self.video_helper.send_get_recordings(ws)
            logger.info(f"[WS] Recordings list received from {drone_id}: {ack}")

            # You can broadcast the retrieved list to other components via the event bus
            # e.g., await self.event_bus.publish(RecordingsListReceived(drone_id, ack.get("recordings", [])))

        except Exception as e:
            await self.event_bus.publish(
                DroneErrorOccurred(drone_id=drone_id, error_message=f"Failed to fetch recordings list: {e}", traceback=traceback.format_exc()))
            logger.error(f"[WS] Failed to fetch recordings list for {drone_id}: {e}")

    async def handle_pull_recordings(self, event: PullRecordingsCommand):
        drone_id = event.drone_id
        try:
            ws = self.connected_clients.get(drone_id)
            if not ws:
                raise DroneCommunicationError(f"Drone {drone_id} is not connected.")

            ack = await self.video_helper.send_pull_recordings(
                ws,
                names=event.names,
                batch_size=getattr(event, "batch_size", None),
                chunk_bytes=getattr(event, "chunk_bytes", None)
            )
            logger.info(f"[WS] Successfully pulled recordings from {drone_id}. Results: {ack.get('processed_results')}")

        except Exception as e:
            await self.event_bus.publish(
                DroneErrorOccurred(drone_id=drone_id, error_message=f"Pull recordings transfer failed: {e}", traceback=traceback.format_exc()))
            logger.error(f"[WS] Pull recordings transfer failed for {drone_id}: {e}")