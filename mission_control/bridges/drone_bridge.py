import asyncio
import base64
import errno
import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

import websockets
from websockets.frames import CloseCode

from mission_control.core.config import Config
from mission_control.core.exceptions import NoDroneConnectedError, DroneCommandFailedError, DroneConnectionLostError, \
     DroneInvalidDataError, DroneCommunicationError
from mission_control.core.mission_context import MissionContext
from mission_control.utils.image_processing import crop_img_square


class DroneBridge:
    """ Handles WebSocket communication between the server and the drone. """

    def __init__(self, config : Config, mission_context: MissionContext):
        self.client = None                      # connected drone.
        self.config = config                    # Configuration variables - dirs, ports, hosts...
        self.mission_context = mission_context  # Place to put where the photo or telemetry is saved.
        self.server = None                      # WebSocket server.
        self.logger = logging.getLogger(__name__)
        self._recording_ack_waiters: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self._recordings_ack_waiters: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self._pull_transfers: Dict[str, Dict[str, Any]] = {}

    ''' ---------- WEBSOCKET LOGIC ---------- '''
    async def start(self):
        """ Starts WebSocket server in the background.

            Raises:
                DroneCommunicationError: If the server cannot be started (e.g., port in use).
        """

        self.logger.info(f"[WS] Starting server on {self.config.host}:{self.config.port}...")

        # Open WebSocket server.
        try:
            serve_kwargs: dict[str, Any] = {
                "max_size": self.config.max_ws_mb * 1024 * 1024,
            }
            ping_interval = getattr(self.config, "ws_ping_interval", None)
            ping_timeout = getattr(self.config, "ws_ping_timeout", None)
            serve_kwargs["ping_interval"] = ping_interval
            serve_kwargs["ping_timeout"] = ping_timeout

            self.server = await websockets.serve(
                self.handler,
                self.config.host,
                self.config.port,
                **serve_kwargs,
            )
            self.logger.info("[WS] Server is running and listening for connections.")

        except OSError as e:
            error_msg = f"[WS] Could not start WebSocket server on port {self.config.port}.\n"
            if e.errno == errno.EADDRINUSE:
                error_msg += "REASON: Port is already in use!\n"
                error_msg += "HINT: Check if another instance is running or wait a moment."
            else:
                error_msg += f"REASON: {e.strerror} (Errno: {e.errno})"

            # Cleanup
            self.server = None

            # Raise the error up the stream.
            raise DroneCommunicationError(error_msg) from e

    async def stop(self):
        """ Closes the server and disconnects connected drone. """

        self.logger.info("[WS] Stopping server...")

        # Closing the server.
        if self.server:
            try:
                self.server.close()
                await self.server.wait_closed()
            except Exception as e:
                # Only log - we still want to disconnect the drone.
                self.logger.warning(f"[WS] Warning: Error checking server close: {e}")

        # Disconnecting the drone.
        if self.client:
            try:
                await self.client.close(code=CloseCode.GOING_AWAY, reason="Server shutdown")
            except websockets.ConnectionClosed:
                # Drone might be already disconnected.
                pass
            except Exception as e:
                self.logger.warning(f"[WS] Warning: Error closing client connection: {e}")
            finally:
                self.client = None

        self.logger.info("[WS] Server stopped.")

    async def handler(self, ws):
        """ Handle connected drone. """

        peer = ws.remote_address # IP address and port of the connected drone.
        if self.client is not None:
            self.logger.info(f"[WS] REJECTED connection from {peer} (System busy)")
            await ws.send("[SERVER] ERROR: System busy. Another drone is already connected.")
            return

        self.client = ws
        self.logger.info(f"[WS] connected: {peer}")

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
                    self.logger.warning(f"[WS] Ignored message (not JSON nor binary): {text}")
                    continue

                if not isinstance(obj, dict):
                    self.logger.warning(f"[WS] Ignored non-dict JSON: {obj}")
                    continue

                match obj:
                    case {"type": "ACK", "of": "COMMAND", "seq": seq, "ok": ok, **ack_rest}:
                        err = ack_rest.get("error")
                        executed = ack_rest.get("executed")
                        self.logger.debug(
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
                        self.logger.warning(f"[WS] message not matching any case.")

        except websockets.ConnectionClosedOK as e:
            self.logger.info(f"[WS] Drone disconnected gracefully: {peer}. {self._format_disconnect_reason(e)}")

        except websockets.ConnectionClosedError as e:
            # This is set when heartbeat has not responded in time.
            self.logger.warning(f"[WS] Drone connection broken: {peer}. {self._format_disconnect_reason(e)}")

        except websockets.ConnectionClosed as e:
            # Fallback for any unexpected ConnectionClosed subtype.
            self.logger.warning(f"[WS] disconnected: {peer}. {self._format_disconnect_reason(e)}")

        except Exception as e:
            self.logger.error(f"[WS] error: {e}.")
        finally:
            # Cleanup
            self._clear_waiters(self._recording_ack_waiters, "Connection lost before ACK")
            self._clear_waiters(self._recordings_ack_waiters, "Connection lost before ACK")
            self._cleanup_pull_transfers()
            # Always reset the client.
            self.client = None

    ''' ---------- AVAILABLE COMMANDS ---------- '''

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(DroneCommunicationError),
        reraise=True
    )
    async def send_message(self, cmd):
        """ Transmits a message to the connected drone via WebSocket.

        :raises:
            NoDroneConnectedError: If no drone is connected.
            DroneCommunicationError: If the message could not be sent.
        """

        self.logger.debug(f"[WS] Trying to send the message: {cmd}")

        if self.client is None:
            raise NoDroneConnectedError("[WS] No drone is connected.")

        try:
            await self.client.send(cmd.upper())
        except Exception as e:
            raise DroneCommunicationError(f"[WS] Failed to send message '{cmd.upper()}'") from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(DroneCommunicationError),
        reraise=True
    )
    async def send_move(self, *, found: bool = False, move=None):
        """ Send command to the drone.

          - found=True  ->  {"type":"COMMAND","action":"FOUND", ...}
          - move=(x,y,z)->  {"type":"COMMAND","move":[x,y,z], ...}
        :raises:
            NoDroneConnectedError: If no drone is connected.
            DroneCommunicationError: If the command could not be sent.
            ValueError: If the move parameters are invalid or no command content is provided.
        """

        self.logger.debug("[WS] Trying to send the move.")

        if self.client is None:
            raise NoDroneConnectedError("[WS] No drone is connected.")

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
                raise ValueError(f"[WS] Invalid move triple {move}: {e}") from e
            payload["move"] = [x, y, z]
        else:
            raise ValueError("[WS] No move content provided (neither 'found' nor 'move').")

        # Sending payload as a JSON.
        try:
            await self.client.send(json.dumps(payload))
        except Exception as e:
            raise DroneCommunicationError("[WS] Sending move failed.") from e

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
            self.logger.debug(
                f"[WS] PULL_RECORDINGS sent to drone "
                f"(files={len(requested_names)}, batch_size={batch}, chunk_bytes={chunk})."
            )
        except Exception as e:
            self._cancel_waiter(self._recordings_ack_waiters, "PULL_RECORDINGS")
            self.logger.error(f"[WS] send failed: {e}")
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
        self.logger.debug(
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
        self.logger.debug(
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
            self.logger.error(f"[WS] begin receive failed for {safe_name}: {exc}")
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
                self.logger.warning(f"[WS] metadata save failed for {safe_name}: {exc}")
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
            self.logger.warning(f"[WS] chunk receive failed for {safe_name}: {exc}")

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
            self.logger.error(f"[WS] finalize receive failed for {safe_name}: {exc}")
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

    async def _handle_binary_photo(self, ws, message):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_base = f"img_{ts}"
        file_name = f"{file_base}.jpg"
        path = os.path.join(self.config.upload_dir, file_name)
        with open(path, "wb") as f:
            f.write(message)
        self.logger.debug(f"[WS] saved binary -> {path}")
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
                self.logger.debug(f"[WS] saved telemetry -> {path}")
            except Exception as e:
                self.logger.warning(f"[WS] error saving telemetry: {e}")

        self.mission_context.last_telemetry_path_cache = path

    async def _handle_telemetry_photo(self, ws, photo_base64, telemetry):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Photo
        if not photo_base64:
            self.logger.warning("[WS] Received 'PHOTO_WITH_TELEMETRY' but 'photo' field is missing; skipping frame.")
            await self._handle_telemetry(telemetry, None)
            return

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
            self.logger.debug(f"[WS] saved *square* photo -> {img_path} ({side}x{side})")
        except Exception as e:
            self.logger.warning(f"[WS] square crop failed, saving raw photo: {e}")
            with open(img_path, "wb") as f:
                f.write(photo_data)
                self.logger.debug(f"[WS] saved photo (raw) -> {img_path}")

        # We are caching paths for easier access after, when sending to VLM.
        self.mission_context.last_photo_path_cache = img_path

        # Telemetry
        await self._handle_telemetry(telemetry, img_file_name)

        if self.mission_context.photo_received_event:
            self.mission_context.photo_received_event.set()
