import asyncio
import base64
import errno
import json
import logging
import os
import subprocess
import traceback

import websockets
from websockets.frames import CloseCode

from mission_control.core.config import Config
from mission_control.core.events import *
from mission_control.core.exceptions import NoDroneConnectedError, DroneCommandFailedError, DroneConnectionLostError, \
    DroneInvalidDataError, DroneCommunicationError
from mission_control.utils.event_bus import EventBus
from mission_control.utils.image_processing import crop_img_square
from mission_control.utils.logger import get_configured_logger

logger = get_configured_logger(__name__)

class WebSocketDroneBridge:
    """ Handles WebSocket communication between the server and the drone. """

    def __init__(self, config : Config, event_bus : EventBus, video_helper: VideoHelper, storage: DataStorageHelper):
        self.connected_clients = {}             # connected drones. {drone_id : ws}
        self.config = config                    # Configuration variables - dirs, ports, hosts...
        self.event_bus = event_bus              # Event bus for event driven architecture.
        self.server = None                      # WebSocket server.
        self.video_helper = video_helper
        self.storage = storage

        # TODO: po co? filmy
        self._recording_ack_waiters: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self._recordings_ack_waiters: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self._pull_transfers: Dict[str, Dict[str, Any]] = {}

        # Subscribe to important events
        event_bus.subscribe(GetPhotoAndTelemetryCommand, self.handle_get_photo_telemetry)
        event_bus.subscribe(ExecuteMoveCommand, self.send_move)

    ''' ---------- WEBSOCKET LOGIC ---------- '''
    async def start(self) -> None:
        """ Starts WebSocket server in the background.

            Raises:
                DroneCommunicationError: If the server cannot be started (e.g., port in use).
        """

        logger.info("[WS] Starting server on %s:%d",self.config.host, self.config.port)

        # Open WebSocket server.
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
        """ Closes the server and disconnects connected drone. """

        logger.info("[WS] Stopping server...")

        # Closing the server.
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        # Cleaning attributes.
        self.connected_clients = {}
        self.server = None

        logger.info("[WS] Server stopped.")

    async def handler(self, ws):
        """ Handle connected drone. """

        peer = ws.remote_address # IP address and port of the connected drone.

        # For now, we only accept one dron at the time.
        if not self.connected_clients  == {}:
            logger.info("[WS] Rejected connection from ",peer, " (System busy)")
            return

        drone_id = len(self.connected_clients)
        self.connected_clients[drone_id] = ws

        logger.info("[WS] connected: ",peer)

        try:
            # Wait for incoming messages.
            # TODO: Define talking protocol with the drone
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
                    logger.warning(f"[WS] Ignored message (not JSON nor binary): {text}")
                    continue

                if not isinstance(obj, dict):
                    logger.warning(f"[WS] Ignored non-dict JSON: {obj}")
                    continue

                match obj:
                    case {"type": "ACK", "of": "COMMAND", "seq": seq, "ok": ok, **ack_rest}:
                        err = ack_rest.get("error")
                        executed = ack_rest.get("executed")
                        logger.debug(
                            f"[ACK ← RPi] COMMAND seq={seq} "
                            f"ok={ok} executed={executed} err={err}"
                        )
                    case {"type": "ACK", "of": "RECORDING", "action": action, "ok": ok, **ack_rest}:
                        self._handle_recording_ack(obj)

                    case {"type": "ACK", "of": "RECORDINGS", "action": action, "ok": ok, **ack_rest}:
                        self._handle_recordings_ack(obj)

                    case {"type": "RECORDING_FILE_BEGIN", "transfer_id": transfer_id, "name": name, **file_rest}:
                        await self._handle_recording_file_begin(
                            transfer_id=str(transfer_id),
                            name=str(name),
                            payload=file_rest,
                        )
                    case {"type": "RECORDING_FILE_CHUNK", "transfer_id": transfer_id, "name": name, "seq": seq, "data": data}:
                        await self._handle_recording_file_chunk(
                            transfer_id=str(transfer_id),
                            name=str(name),
                            seq=int(seq),
                            chunk_b64=str(data),
                        )
                    case {"type": "RECORDING_FILE_END", "transfer_id": transfer_id, "name": name, **end_rest}:
                        await self._handle_recording_file_end(
                            transfer_id=str(transfer_id),
                            name=str(name),
                            payload=end_rest,
                        )

                    case {"type": "TELEMETRY", "data": data}:
                        await self._handle_telemetry(data)

                    case {"type": "PHOTO_WITH_TELEMETRY", "photo": photo, "telemetry": telemetry}:
                        await self._handle_telemetry_photo(ws, photo, telemetry)

                    case _:
                        logger.warning(f"[WS] message not matching any case.")

        # TODO: publish disconnected event?
        except websockets.ConnectionClosedOK as e:
            logger.info(f"[WS] Drone disconnected gracefully: {peer}. {self._format_disconnect_reason(e)}")

        except websockets.ConnectionClosedError as e:
            # This is set when heartbeat has not responded in time.
            logger.warning(f"[WS] Drone connection broken: {peer}. {self._format_disconnect_reason(e)}")

        except websockets.ConnectionClosed as e:
            # Fallback for any unexpected ConnectionClosed subtype.
            logger.warning(f"[WS] disconnected: {peer}. {self._format_disconnect_reason(e)}")

        except Exception as e:
            logger.error(f"[WS] error: {e}.")
        finally:
            # Cleanup
            self._clear_waiters(self._recording_ack_waiters, "Connection lost before ACK")
            self._clear_waiters(self._recordings_ack_waiters, "Connection lost before ACK")
            self._cleanup_pull_transfers()
            # Always reset the client.
            self.connected_clients.pop(drone_id, None)

    ''' ---------- SUBSCRIBED COMMANDS ---------- '''

    async def handle_get_photo_telemetry(self, event: GetPhotoAndTelemetryCommand):
        await self.send_message(event.drone_id, "PHOTO_WITH_TELEMETRY")

    async def send_message(self, drone_id: str, cmd: str) -> None:
        """ Transmits a message to the connected drone via WebSocket.
        Handler for SendMessageToDroneCommand.

        :publishes:
            DroneErrorOccurred: If no drone is connected or the message could not be sent.
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
        """ Send command to the drone.
        Handler for ExecuteMoveCommand.

        :raises:
            ValueError: If the move parameters are invalid or no command content is provided.
        """
        drone_id = event.drone_id
        move = event.move

        try:
            if drone_id not in self.connected_clients:
                raise DroneCommunicationError(f"Drone {drone_id} is not connected.")
            client = self.connected_clients[drone_id]

            # TODO: define how to send the move to the drone.
            payload: Dict[str, Any] = {
                "type": "COMMAND",
                "ts": datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
            }

            if move is not None:
                try:
                    x, y, z = map(float, move)
                except (ValueError, IndexError, TypeError) as e:
                    raise ValueError(f"[WS] Invalid move triple {move}: {e}") from e
                payload["move"] = [x, y, z]
            else:
                raise ValueError("[WS] No move content provided (neither 'found' nor 'move').")

            # Sending payload as a JSON.
            await client.send(json.dumps(payload))

        except Exception as e:
            error_event = DroneErrorOccurred(
                drone_id=drone_id,
                error_message=f"[WS] Failed to send move to drone {drone_id}: {str(e)}",
                traceback=traceback.format_exc()
            )
            await self.event_bus.publish(error_event)
            logger.error(f"[WS] Failed to send move to drone {drone_id}: {e}")

    ''' --------------------------------------------------------------------------- '''

    # TODO: Czy to musi być osobno?
    async def send_recording_command(self, cmd: str, timeout_sec: float = 5.0) -> Dict[str, Any]:
        cmd_upper = cmd.upper()
        if cmd_upper not in ("START_RECORDING", "STOP_RECORDING"):
            raise ValueError(f"Unsupported recording command: {cmd}")
        if cmd_upper == "STOP_RECORDING":
            # Hardcoded longer timeout for stop-finalization on the RPi side.
            timeout_sec = 20.0
        if self.client is None:
            raise NoDroneConnectedError("No drone is connected.")

        #TODO: za co odpowiedzialne jest to?
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        previous = self._recording_ack_waiters.get(cmd_upper)
        if previous is not None and not previous.done():
            previous.cancel()
        self._recording_ack_waiters[cmd_upper] = waiter

        try:
            await self.send_message(cmd_upper)
        except DroneCommandFailedError:
            self._cancel_waiter(self._recording_ack_waiters, cmd_upper)
            raise

        try:
            ack = await asyncio.wait_for(waiter, timeout=timeout_sec)
        except asyncio.TimeoutError as exc:
            self._cancel_waiter(self._recording_ack_waiters, cmd_upper)
            raise DroneCommandFailedError(
                f"Timed out waiting for {cmd_upper} ACK from drone."
            ) from exc

        if not bool(ack.get("ok", False)):
            raise DroneCommandFailedError(
                f"{cmd_upper} failed on drone: {ack.get('error', 'unknown error')}"
            )
        return ack

    async def send_get_recordings(self, timeout_sec: float = 5.0) -> Dict[str, Any]:
        if self.client is None:
            raise NoDroneConnectedError("No drone is connected.")

        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        previous = self._recordings_ack_waiters.get("GET_RECORDINGS")
        if previous is not None and not previous.done():
            previous.cancel()
        self._recordings_ack_waiters["GET_RECORDINGS"] = waiter

        try:
            await self.send_message("GET_RECORDINGS")
        except DroneCommandFailedError:
            self._cancel_waiter(self._recordings_ack_waiters, "GET_RECORDINGS")
            raise

        try:
            ack = await asyncio.wait_for(waiter, timeout=timeout_sec)
        except asyncio.TimeoutError as exc:
            self._cancel_waiter(self._recordings_ack_waiters, "GET_RECORDINGS")
            raise DroneCommandFailedError(
                "Timed out waiting for GET_RECORDINGS ACK from drone."
            ) from exc

        if not bool(ack.get("ok", False)):
            raise DroneCommandFailedError(
                f"GET_RECORDINGS failed on drone: {ack.get('error', 'unknown error')}"
            )
        return ack

    async def send_pull_recordings(
        self,
        *,
        names: list[str],
        batch_size: int | None = None,
        chunk_bytes: int | None = None,
        timeout_sec: float = 300.0,
    ) -> Dict[str, Any]:
        if self.client is None:
            raise NoDroneConnectedError("No drone is connected.")

        requested_names = [name for name in names if isinstance(name, str) and name.strip()]
        if not requested_names:
            raise ValueError("No valid recording names provided.")

        batch = int(batch_size) if batch_size is not None else int(self.config.pull_batch_size)
        chunk = int(chunk_bytes) if chunk_bytes is not None else int(self.config.pull_chunk_bytes)

        batch = max(1, min(batch, 32))
        chunk = max(64 * 1024, min(chunk, 2 * 1024 * 1024))

        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        previous = self._recordings_ack_waiters.get("PULL_RECORDINGS")
        if previous is not None and not previous.done():
            previous.cancel()
        self._recordings_ack_waiters["PULL_RECORDINGS"] = waiter

        payload = {
            "type": "RECORDINGS",
            "action": "PULL_RECORDINGS",
            "names": requested_names,
            "batch_size": batch,
            "chunk_bytes": chunk,
        }

        try:
            await self.client.send(json.dumps(payload))
            logger.debug(
                f"[WS] PULL_RECORDINGS sent to drone "
                f"(files={len(requested_names)}, batch_size={batch}, chunk_bytes={chunk})."
            )
        except Exception as e:
            self._cancel_waiter(self._recordings_ack_waiters, "PULL_RECORDINGS")
            logger.error(f"[WS] send failed: {e}")
            raise DroneCommandFailedError("Failed to send PULL_RECORDINGS to the drone") from e

        try:
            ack = await asyncio.wait_for(waiter, timeout=timeout_sec)
        except asyncio.TimeoutError as exc:
            self._cancel_waiter(self._recordings_ack_waiters, "PULL_RECORDINGS")
            raise DroneCommandFailedError(
                "Timed out waiting for PULL_RECORDINGS ACK from drone."
            ) from exc

        transfer_id = ack.get("transfer_id")
        processed_results = await self._finalize_pull_transfer(transfer_id=str(transfer_id) if transfer_id else None)
        ack["processed_results"] = processed_results
        return ack

    # SAVING PHOTOS/JSON IS BLOCKING - WITH MULTIPLE DRONES OR BIG DATA COULD BE BAD
    # may need change into run_in_executor

    ''' ---------- HELPER METHODS ----------'''

    async def _safe_send(self, client, msg: str, drone_id: int) -> bool:
        """ Tries to send the message to the drone.
        On failure, returns False and publishes DroneErrorOccurred including traceback.
        """

        try:
            await client.send(msg)
            return True
        except Exception as e:
            logger.error(f"[WS] Unable to send '{msg}' to drone {drone_id}.", exc_info=True)

            err_event = DroneErrorOccurred(
                drone_id=drone_id,
                error_message=f"[WS] Failed to send message '{msg}'. Error: {str(e)}",
                traceback=traceback.format_exc()
            )
            await self.event_bus.publish(err_event)
            return False

    async def _ensure_drone_connected(self, drone_id: int):
        """ Check if client of given id is connected.
        If not, publishes DroneErrorOccurred with proper message,
        logs it as warning and returns False.
        """
        client = self.connected_clients.get(drone_id, None)
        if client is None:
            logger.warning("[WS] No drone is connected.")
            err_event = DroneErrorOccurred(
                drone_id = drone_id,
                error_message = "[WS] No drone is connected."
            )
            await self.event_bus.publish(err_event)
            return False
        return True

    @staticmethod
    def _format_disconnect_reason(exc: websockets.ConnectionClosed) -> str:
        code = None
        reason = ""

        rcvd = getattr(exc, "rcvd", None)
        sent = getattr(exc, "sent", None)
        if rcvd is not None:
            code = getattr(rcvd, "code", None)
            reason = (getattr(rcvd, "reason", "") or "").strip()
        elif sent is not None:
            code = getattr(sent, "code", None)
            reason = (getattr(sent, "reason", "") or "").strip()
        elif isinstance(exc, websockets.ConnectionClosedError):
            # No close frame exchanged.
            code = CloseCode.ABNORMAL_CLOSURE

        details = str(exc)

        if code is None:
            return f"details={details}"
        if reason:
            return f"code={code}, reason={reason}, details={details}"
        return f"code={code}, details={details}"

    def _cancel_waiter(self, waiter_dict: Dict[str, asyncio.Future], key: str):
        """Safely removes and cancels a waiter future."""
        waiter = waiter_dict.pop(key, None)
        if waiter is not None and not waiter.done():
            waiter.cancel()

    def _handle_recording_ack(self, ack: Dict[str, Any]):
        """Handles ACKs for RECORDING actions and resolves the corresponding waiter."""
        action = ack.get("action")
        ok = ack.get("ok")
        logger.debug(
            f"[ACK ← RPi] RECORDING action={action} ok={ok} "
            f"recording={ack.get('recording')} ref_count={ack.get('ref_count')} "
            f"path={ack.get('path')} err={ack.get('error')}"
        )
        waiter = self._recording_ack_waiters.pop(str(action), None)
        if waiter is not None and not waiter.done():
            waiter.set_result(ack)

    def _handle_recordings_ack(self, ack: Dict[str, Any]):
        """Handles ACKs for RECORDINGS actions and resolves the corresponding waiter."""
        action = ack.get("action")
        ok = ack.get("ok")
        logger.debug(
            f"[ACK ← RPi] RECORDINGS action={action} ok={ok} "
            f"count={ack.get('count')} completed={ack.get('completed_count')} "
            f"err={ack.get('error')}"
        )
        waiter = self._recordings_ack_waiters.pop(str(action), None)
        if waiter is not None and not waiter.done():
            waiter.set_result(ack)

    def _clear_waiters(self, waiters: Dict[str, asyncio.Future], reason: str):
        """Cancels all pending waiters in a dictionary with a connection lost error."""
        for key, waiter in list(waiters.items()):
            if not waiter.done():
                waiter.set_exception(
                    DroneConnectionLostError(f"{reason} for {key}.")
                )
        waiters.clear()

    async def _handle_recording_file_begin(self, *, transfer_id: str, name: str, payload: dict[str, Any]) -> None:
        transfer = self._pull_transfers.setdefault(
            transfer_id,
            {"active_files": {}, "completed": {}, "receive_errors": {}},
        )
        active_files = transfer["active_files"]
        completed = transfer["completed"]

        safe_name = Path(name).name
        raw_path = Path(self.config.recordings_raw_dir) / safe_name
        tmp_path = raw_path.with_suffix(raw_path.suffix + ".part")

        try:
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            if tmp_path.exists():
                tmp_path.unlink()
            fh = tmp_path.open("wb")
        except Exception as exc:
            transfer["receive_errors"][safe_name] = f"begin_failed: {exc}"
            logger.error(f"[WS] begin receive failed for {safe_name}: {exc}")
            return

        metadata_obj = payload.get("metadata")
        metadata = metadata_obj if isinstance(metadata_obj, dict) else None
        metadata_path: Path | None = None
        if metadata is not None:
            metadata_path = Path(self.config.recordings_meta_dir) / f"{raw_path.stem}.json"
            try:
                metadata_path.parent.mkdir(parents=True, exist_ok=True)
                with metadata_path.open("w", encoding="utf-8") as file_obj:
                    json.dump(metadata, file_obj, ensure_ascii=False, indent=2)
            except Exception as exc:
                logger.warning(f"[WS] metadata save failed for {safe_name}: {exc}")
                metadata_path = None

        active_files[safe_name] = {
            "fh": fh,
            "tmp_path": tmp_path,
            "raw_path": raw_path,
            "bytes_received": 0,
            "chunks_received": 0,
            "metadata": metadata,
            "metadata_path": metadata_path,
            "size_bytes_expected": int(payload.get("size_bytes", 0)),
        }
        completed.pop(safe_name, None)

    async def _handle_recording_file_chunk(
        self,
        *,
        transfer_id: str,
        name: str,
        seq: int,
        chunk_b64: str,
    ) -> None:
        transfer = self._pull_transfers.get(transfer_id)
        if not transfer:
            return

        safe_name = Path(name).name
        state = transfer.get("active_files", {}).get(safe_name)
        if not isinstance(state, dict):
            transfer["receive_errors"][safe_name] = "chunk_for_unknown_file"
            return

        handle = state.get("fh")
        if handle is None:
            transfer["receive_errors"][safe_name] = "missing_file_handle"
            return

        try:
            chunk = base64.b64decode(chunk_b64)
            handle.write(chunk)
            state["bytes_received"] = int(state.get("bytes_received", 0)) + len(chunk)
            state["chunks_received"] = max(int(state.get("chunks_received", 0)), seq + 1)
        except Exception as exc:
            transfer["receive_errors"][safe_name] = f"chunk_failed: {exc}"
            logger.warning(f"[WS] chunk receive failed for {safe_name}: {exc}")

    async def _handle_recording_file_end(
        self,
        *,
        transfer_id: str,
        name: str,
        payload: dict[str, Any],
    ) -> None:
        transfer = self._pull_transfers.get(transfer_id)
        if not transfer:
            return

        safe_name = Path(name).name
        active_files = transfer.get("active_files", {})
        state = active_files.pop(safe_name, None)
        if not isinstance(state, dict):
            transfer["receive_errors"][safe_name] = "end_for_unknown_file"
            return

        handle = state.get("fh")
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

        tmp_path = state.get("tmp_path")
        raw_path = state.get("raw_path")
        try:
            if isinstance(tmp_path, Path) and isinstance(raw_path, Path):
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path.replace(raw_path)
        except Exception as exc:
            transfer["receive_errors"][safe_name] = f"finalize_failed: {exc}"
            logger.error(f"[WS] finalize receive failed for {safe_name}: {exc}")
            return

        transfer["completed"][safe_name] = {
            "raw_path": str(raw_path) if isinstance(raw_path, Path) else None,
            "metadata": state.get("metadata"),
            "metadata_path": str(state.get("metadata_path")) if isinstance(state.get("metadata_path"), Path) else None,
            "bytes_received": int(state.get("bytes_received", 0)),
            "chunks_received": int(state.get("chunks_received", 0)),
            "expected_chunks": int(payload.get("chunks", 0)),
            "size_bytes_expected": int(state.get("size_bytes_expected", 0)),
        }

    def _cleanup_pull_transfers(self):
        """Closes any open file handles from incomplete pull transfers."""
        for transfer in self._pull_transfers.values():
            active_files = transfer.get("active_files", {})
            if not isinstance(active_files, dict):
                continue
            for file_state in active_files.values():
                if not isinstance(file_state, dict):
                    continue
                handle = file_state.get("fh")
                if handle is not None:
                    try:
                        handle.close()
                    except Exception:
                        pass  # Ignore errors on close
        self._pull_transfers.clear()

    async def _finalize_pull_transfer(self, *, transfer_id: str | None) -> list[dict[str, Any]]:
        if not transfer_id:
            return []

        transfer = self._pull_transfers.pop(transfer_id, None)
        if not isinstance(transfer, dict):
            return []

        completed = transfer.get("completed", {})
        receive_errors = transfer.get("receive_errors", {})
        if not isinstance(completed, dict):
            completed = {}
        if not isinstance(receive_errors, dict):
            receive_errors = {}

        results: list[dict[str, Any]] = []
        names = set(completed.keys()) | set(receive_errors.keys())
        for name in sorted(names):
            summary = await self._process_pulled_file(name, completed, receive_errors)
            results.append(summary)

        return results

    async def _process_pulled_file(self, name: str, completed: dict, receive_errors: dict) -> dict[str, Any]:
        """Processes a single pulled file: validates transfer and converts to MP4."""
        file_state = completed.get(name, {})
        if not isinstance(file_state, dict):
            file_state = {}

        raw_path_value = file_state.get("raw_path")
        raw_path = Path(raw_path_value) if isinstance(raw_path_value, str) else None

        summary: dict[str, Any] = {
            "name": name,
            "pulled_ok": False,
            "raw_path": str(raw_path) if raw_path is not None else None,
            "metadata_path": file_state.get("metadata_path"),
            "size_bytes": int(file_state.get("bytes_received", 0)),
            "chunks": int(file_state.get("chunks_received", 0)),
        }

        receive_error = receive_errors.get(name)
        if receive_error is not None:
            summary["pull_error"] = str(receive_error)
            summary["convert_ok"] = False
            return summary

        if raw_path is None or not raw_path.exists():
            summary["pull_error"] = "raw_file_missing_after_transfer"
            summary["convert_ok"] = False
            return summary

        summary["pulled_ok"] = True
        metadata = file_state.get("metadata")
        if not isinstance(metadata, dict):
            metadata = None

        try:
            conversion = await self._convert_raw_recording(raw_path=raw_path, metadata=metadata)
            summary.update(conversion)
        except Exception as e:
            summary["convert_ok"] = False
            summary["convert_error"] = f"Conversion process failed: {e}"

        return summary

    async def _convert_raw_recording(self, *, raw_path: Path, metadata: dict[str, Any] | None) -> dict[str, Any]:
        mp4_path = Path(self.config.recordings_mp4_dir) / f"{raw_path.stem}.mp4"
        fps = self._resolve_recording_fps(metadata)
        result = await asyncio.to_thread(self._run_ffmpeg_conversion, raw_path, mp4_path, fps)
        result["mp4_path"] = str(mp4_path)
        result["fps_used"] = fps
        return result

    @staticmethod
    def _run_ffmpeg_conversion(raw_path: Path, mp4_path: Path, fps: int) -> dict[str, Any]:
        mp4_path.parent.mkdir(parents=True, exist_ok=True)

        remux_cmd = [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(raw_path),
            "-c",
            "copy",
            str(mp4_path),
        ]
        try:
            remux = subprocess.run(remux_cmd, check=False, capture_output=True, text=True)
        except FileNotFoundError:
            return {"convert_ok": False, "convert_error": "ffmpeg_not_found"}
        except Exception as exc:
            return {"convert_ok": False, "convert_error": str(exc)}

        if remux.returncode == 0:
            return {"convert_ok": True, "convert_mode": "copy"}

        reencode_cmd = [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(raw_path),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(mp4_path),
        ]
        reencode = subprocess.run(reencode_cmd, check=False, capture_output=True, text=True)
        if reencode.returncode == 0:
            return {"convert_ok": True, "convert_mode": "reencode"}

        stderr = (reencode.stderr or "").strip()
        if not stderr:
            stderr = (remux.stderr or "").strip()
        return {"convert_ok": False, "convert_error": stderr or "ffmpeg_failed"}

    def _resolve_recording_fps(self, metadata: dict[str, Any] | None) -> int:
        if metadata is None:
            return max(1, int(self.config.record_fps_default))
        raw_fps = metadata.get("record_fps")
        if isinstance(raw_fps, int):
            return max(1, raw_fps)
        if isinstance(raw_fps, str):
            try:
                return max(1, int(raw_fps))
            except ValueError:
                return max(1, int(self.config.record_fps_default))
        return max(1, int(self.config.record_fps_default))