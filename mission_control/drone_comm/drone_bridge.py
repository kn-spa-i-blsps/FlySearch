import asyncio
import errno
import itertools
import json
import traceback
from collections import defaultdict

import websockets
from websockets import ConnectionClosedError, ConnectionClosedOK, ConnectionClosed

from mission_control.core.config import Config
from mission_control.core.events import *
from mission_control.core.exceptions import DroneCommunicationError, DroneInvalidDataError
from mission_control.core.interfaces import DataStorageHelper
from mission_control.drone_comm.video_helper import VideoHelper
from mission_control.core.event_bus import EventBus
from mission_control.utils.logger import get_configured_logger

logger = get_configured_logger(__name__)


class WebSocketDroneBridge:
    """ Handles WebSocket communication between the server and the drone. """

    def __init__(self, config: Config, event_bus: EventBus, video_helper: VideoHelper, storage: DataStorageHelper):
        self.connected_clients: Dict[str, Any] = {}  # Active drone connections mapping {drone_id: ws_instance}
        self.disconnected_clients = set()  # Tracks drones that lost connection abnormally
        self.config = config  # System configuration
        self.event_bus = event_bus
        self.server = None  # WebSocket server instance
        self.video_helper = video_helper
        self.storage = storage

        # Sequence number generator (starting from 1000)
        self._seq_counters = defaultdict(lambda: itertools.count(1000))

        # Subscribe to standard core commands
        self.event_bus.subscribe(GetPhotoAndTelemetryCommand, self.handle_get_photo_telemetry)
        self.event_bus.subscribe(ExecuteMoveCommand, self.send_move)

        # Subscribe to video-related commands
        self.event_bus.subscribe(StartRecordingCommand, self.handle_start_recording)
        self.event_bus.subscribe(StopRecordingCommand, self.handle_stop_recording)
        self.event_bus.subscribe(GetRecordingsListCommand, self.handle_get_recordings)
        self.event_bus.subscribe(PullRecordingsCommand, self.handle_pull_recordings)

    def _get_seq(self, drone_id: str) -> int:
        return next(self._seq_counters[drone_id])

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
        drone_id = None
        peer = ws.remote_address
        logger.info(f"[WS] New connection from {peer}. Waiting for authorization...")
        try:
            drone_id = await self._authenticate_drone(ws, peer)
        except (json.JSONDecodeError, asyncio.TimeoutError) as e:
            logger.warning(f"[WS] Error or timeout during authorization with {peer}: {e}")
            return

        if not drone_id:
            return

        try:
            # Message Listening Loop
            async for message in ws:
                try:
                    obj = json.loads(message.strip())
                except json.JSONDecodeError:
                    logger.warning(f"[WS] Ignored message (not JSON nor binary): {message}")
                    continue

                if isinstance(obj, dict):
                    await self._process_message(ws, drone_id, obj)
        except ConnectionClosedOK as e:
            logger.info(f"[WS] Drone disconnected gracefully: {peer}. {self.video_helper.format_disconnect_reason(e)}")
            await self.event_bus.publish(DroneDisconnected(drone_id=drone_id))
        except ConnectionClosedError as e:
            logger.warning(f"[WS] Drone connection broken: {peer}. {self.video_helper.format_disconnect_reason(e)}")
            if drone_id:
                self.disconnected_clients.add(drone_id)
                await self.event_bus.publish(DroneConnectionLost(drone_id=drone_id))
        except ConnectionClosed as e:
            logger.warning(f"[WS] disconnected: {peer}. {self.video_helper.format_disconnect_reason(e)}")
        except Exception as e:
            logger.error(f"[WS] error: {e}")
            logger.debug(traceback.format_exc())
        finally:
            self.video_helper.clear_waiters(self.video_helper.recording_ack_waiters, "Connection lost before ACK")
            self.video_helper.clear_waiters(self.video_helper.recordings_ack_waiters, "Connection lost before ACK")
            self.video_helper.cleanup_pull_transfers()

            if drone_id is not None and self.connected_clients.get(drone_id) == ws:
                self.connected_clients.pop(drone_id, None)
                logger.info(f"[WS] Drone {drone_id} removed from the connected clients registry.")

    ''' ---------- SUBSCRIBED COMMANDS (CORE) ---------- '''

    async def send_message(self, drone_id: str, payload: dict) -> None:
        """
        Transmits a JSON dictionary to the connected drone via WebSocket.
        """
        if drone_id not in self.connected_clients:
            raise DroneCommunicationError(f"Drone {drone_id} is not connected.")
        client = self.connected_clients[drone_id]
        await client.send(json.dumps(payload))

    async def handle_get_photo_telemetry(self, event: GetPhotoAndTelemetryCommand):
        payload = {
            "type": "COMMAND",
            "action": "GET_PHOTO_TELEMETRY",
            "seq": self._get_seq(event.drone_id)
        }
        logger.info(f"[WS] Sending GET_PHOTO_TELEMETRY to {event.drone_id}")
        try:
            await self.send_message(event.drone_id, payload)
        except ConnectionClosedOK as e:
            await self.event_bus.publish(DroneDisconnected(drone_id=event.drone_id))
        except ConnectionClosedError as e:
            await self.event_bus.publish(DroneConnectionLost(drone_id=event.drone_id))
        except DroneCommunicationError:
            if event.drone_id in self.disconnected_clients:
                await self.event_bus.publish(DroneConnectionLost(drone_id=event.drone_id))
        except Exception as e:
            error_event = DroneErrorOccurred(
                drone_id=event.drone_id,
                error_message=f"[WS] Failed to send get_photo_telemetry request to drone {event.drone_id}: {str(e)}",
                traceback=traceback.format_exc()
            )
            await self.event_bus.publish(error_event)
            logger.error(f"[WS] Failed to send get_photo_telemetry request to drone {event.drone_id}: {e}")

    async def send_move(self, event: ExecuteMoveCommand):
        """ Sends a movement command vector to the drone. """
        drone_id = event.drone_id
        move = event.move

        try:
            payload: Dict[str, Any] = {
                "type": "COMMAND",
                "action": "MOVE",
                "seq": self._get_seq(drone_id)
            }

            if move is not None:
                try:
                    x, y, z = map(float, move)
                except (ValueError, IndexError, TypeError) as e:
                    raise ValueError(f"[WS] Invalid move coordinates {move}: {e}") from e
                payload["move"] = [x, y, z]
            else:
                raise ValueError("[WS] No move content provided.")

            await self.send_message(drone_id, payload)
        except ConnectionClosedOK as e:
            await self.event_bus.publish(DroneDisconnected(drone_id=drone_id))
        except ConnectionClosedError as e:
            await self.event_bus.publish(DroneConnectionLost(drone_id=drone_id))
        except DroneCommunicationError:
            if drone_id in self.disconnected_clients:
                await self.event_bus.publish(DroneConnectionLost(drone_id=drone_id))
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
                DroneErrorOccurred(drone_id=drone_id, error_message=f"Start recording failed: {e}",
                                   traceback=traceback.format_exc()))
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
                DroneErrorOccurred(drone_id=drone_id, error_message=f"Stop recording failed: {e}",
                                   traceback=traceback.format_exc()))
            logger.error(f"[WS] Stop recording failed for {drone_id}: {e}")

    async def handle_get_recordings(self, event: GetRecordingsListCommand):
        drone_id = event.drone_id
        try:
            ws = self.connected_clients.get(drone_id)
            if not ws:
                raise DroneCommunicationError(f"Drone {drone_id} is not connected.")
            ack = await self.video_helper.send_get_recordings(ws)
            logger.info(f"[WS] Recordings list received from {drone_id}: {ack}")
            await self.event_bus.publish(RecordingsListReceived(
                drone_id=drone_id,
                recordings=ack.get("recordings", [])
            ))
        except Exception as e:
            await self.event_bus.publish(RecordingsListReceived(drone_id=drone_id, error=str(e)))
            await self.event_bus.publish(
                DroneErrorOccurred(drone_id=drone_id, error_message=f"Failed to fetch recordings list: {e}",
                                   traceback=traceback.format_exc()))
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
            await self.event_bus.publish(RecordingsPullCompleted(
                drone_id=drone_id,
                results=ack.get("processed_results", [])
            ))
        except Exception as e:
            await self.event_bus.publish(RecordingsPullCompleted(drone_id=drone_id, error=str(e)))
            await self.event_bus.publish(
                DroneErrorOccurred(drone_id=drone_id, error_message=f"Pull recordings transfer failed: {e}",
                                   traceback=traceback.format_exc()))
            logger.error(f"[WS] Pull recordings transfer failed for {drone_id}: {e}")

    async def _authenticate_drone(self, ws, peer) -> str | None:
        # Drone Identification (Handshake / Auth)
        first_msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
        if isinstance(first_msg, (bytes, bytearray)):
            logger.warning(f"[WS] Expected JSON AUTH payload, but received binary data from {peer}.")
            await self._send_ack(ws, of="AUTH", ok=False)
            return None

        auth_obj = json.loads(first_msg.strip())

        if auth_obj.get("type") == "AUTH" and "drone_id" in auth_obj:
            drone_id = auth_obj["drone_id"]

            if self.connected_clients.get(drone_id):
                logger.warning(f"[WS] Drone {drone_id} is already connected. Rejecting connection from {peer}.")
                await self._send_ack(ws, of="AUTH", ok=False)
                return None

            self.connected_clients[drone_id] = ws
            logger.info(f"[WS] Authorization successful. Registered drone: {drone_id}")

            if drone_id in self.disconnected_clients:
                logger.info(f"[WS] Drone {drone_id} reconnected.")
                self.disconnected_clients.remove(drone_id)
                await self.event_bus.publish(DroneReconnected(drone_id=drone_id))

            await self._send_ack(ws, of="AUTH", ok=True)
            return drone_id
        else:
            logger.warning(f"[WS] Invalid AUTH payload from {peer}: {auth_obj}")
            await self._send_ack(ws, of="AUTH", ok=False)
            return None

    @staticmethod
    async def _send_ack(ws, of: str, ok: bool = True, seq: int = None, error: str = None) -> None:
        payload = {
            "type": "ACK",
            "of": of,
            "ok": ok
        }
        if seq is not None:
            payload["seq"] = seq
        if error is not None:
            payload["error"] = error

        await ws.send(json.dumps(payload))

    async def _process_message(self, ws, drone_id: str, obj: dict) -> None:
        # Route messages based on the "type" key
        match obj:
            case {"type": "ACK", "of": "COMMAND", "seq": seq, "ok": ok, **ack_rest}:
                action = ack_rest.get("action", "UNKNOWN")
                err = ack_rest.get("error")
                logger.debug(
                    f"[ACK ← {drone_id}] COMMAND action={action} seq={seq} "
                    f"ok={ok} err={err}"
                )
                if action == "MOVE" and ok:
                    await self.event_bus.publish(MoveStarted(drone_id=drone_id))

            case {"type": "MOVE_EXECUTED", "seq": seq, "ok": ok}:
                logger.debug(
                    f"[ACK ← {drone_id}] MOVE_EXECUTED seq={seq} "
                    f"ok={ok}"
                )
                if ok:
                    await self.event_bus.publish(MoveExecuted(drone_id=drone_id))

                await self._send_ack(ws, of="MOVE_EXECUTED", ok=True, seq=seq)

            case {"type": "PHOTO_WITH_TELEMETRY", "seq": seq, "photo": photo, "telemetry": telemetry}:
                logger.info(f"[WS] Received PHOTO_WITH_TELEMETRY from {drone_id} (seq: {seq})")
                try:
                    photo_path, telemetry_path = await self.storage.save_photo_and_telemetry(photo, telemetry)
                    await self.event_bus.publish(PhotoWithTelemetryReceived(
                        drone_id=drone_id,
                        photo_path=photo_path,
                        telemetry_path=telemetry_path
                    ))

                    await self._send_ack(ws, of="PHOTO_WITH_TELEMETRY", ok=True, seq=seq)
                except DroneInvalidDataError as e:
                    logger.error(f"[WS] Error saving photo/telemetry for {drone_id}: {e}")
                    await self._send_ack(ws, of="PHOTO_WITH_TELEMETRY", ok=False, seq=seq, error=str(e))

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

            case {"type": "ERROR", "message": message}:
                logger.warning(f"[WS] Drone {drone_id} reported an error: {message}")

            case _:
                logger.warning(f"[WS] Unrecognized message type from {drone_id}: {obj.get('type')}")
