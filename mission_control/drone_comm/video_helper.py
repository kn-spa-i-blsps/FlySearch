import asyncio
import logging
import json
import base64
import subprocess
from pathlib import Path
from typing import Dict, Any

import websockets
from websockets.frames import CloseCode

from mission_control.core.exceptions import NoDroneConnectedError, DroneCommandFailedError, DroneConnectionLostError

logger = logging.getLogger(__name__)


class VideoHelper:
    """
    Helper class to manage video recording commands and asynchronous file transfers
    between the server and the drone via WebSockets.
    """

    def __init__(self, config, event_bus):
        self.config = config
        self.event_bus = event_bus

        # Dictionaries holding asyncio.Future objects. We use these to bridge
        # the gap between sending a request and waiting for an ACK from the drone.
        self.recording_ack_waiters: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self.recordings_ack_waiters: Dict[str, asyncio.Future[Dict[str, Any]]] = {}

        # State tracker for incoming file transfers (chunks being pieced together)
        self._pull_transfers: Dict[str, Dict[str, Any]] = {}

    async def send_recording_command(self, ws, cmd: str, timeout_sec: float = 5.0) -> Dict[str, Any]:
        """
        Sends a START or STOP recording command to the drone and waits for the acknowledgment.
        """
        cmd_upper = cmd.upper()
        if cmd_upper not in ("START_RECORDING", "STOP_RECORDING"):
            raise ValueError(f"Unsupported recording command: {cmd}")

        if cmd_upper == "STOP_RECORDING":
            # Stopping a recording usually takes longer as the drone needs to finalize the file
            timeout_sec = 20.0

        if ws is None:
            raise NoDroneConnectedError("No drone is connected.")

        # Set up a Future to wait for the ACK from the drone.
        # If there's already a pending request for this command, cancel it to avoid conflicts.
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        previous = self.recording_ack_waiters.get(cmd_upper)
        if previous is not None and not previous.done():
            previous.cancel()
        self.recording_ack_waiters[cmd_upper] = waiter

        try:
            await ws.send(cmd_upper)
        except Exception as e:
            # Bail out early and clean up the future if the send itself fails
            self._cancel_waiter(self.recording_ack_waiters, cmd_upper)
            raise DroneCommandFailedError(f"Failed to send command {cmd_upper}") from e

        # Wait for the drone to respond or timeout
        try:
            ack = await asyncio.wait_for(waiter, timeout=timeout_sec)
        except asyncio.TimeoutError as exc:
            self._cancel_waiter(self.recording_ack_waiters, cmd_upper)
            raise DroneCommandFailedError(
                f"Timed out waiting for {cmd_upper} ACK from drone."
            ) from exc

        if not ack.get("ok", False):
            raise DroneCommandFailedError(
                f"{cmd_upper} failed on drone: {ack.get('error', 'unknown error')}"
            )

        return ack

    async def send_get_recordings(self, ws, timeout_sec: float = 5.0) -> Dict[str, Any]:
        """Fetches the list of available recordings from the drone."""
        if ws is None:
            raise NoDroneConnectedError("No drone is connected.")

        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        previous = self.recordings_ack_waiters.get("GET_RECORDINGS")
        if previous is not None and not previous.done():
            previous.cancel()
        self.recordings_ack_waiters["GET_RECORDINGS"] = waiter

        try:
            await ws.send("GET_RECORDINGS")
        except Exception as e:
            self._cancel_waiter(self.recordings_ack_waiters, "GET_RECORDINGS")
            raise DroneCommandFailedError("Failed to send GET_RECORDINGS") from e

        try:
            ack = await asyncio.wait_for(waiter, timeout=timeout_sec)
        except asyncio.TimeoutError as exc:
            self._cancel_waiter(self.recordings_ack_waiters, "GET_RECORDINGS")
            raise DroneCommandFailedError(
                "Timed out waiting for GET_RECORDINGS ACK from drone."
            ) from exc

        if not ack.get("ok", False):
            raise DroneCommandFailedError(
                f"GET_RECORDINGS failed on drone: {ack.get('error', 'unknown error')}"
            )
        return ack

    async def send_pull_recordings(
            self,
            ws,
            *,
            names: list[str],
            batch_size: int | None = None,
            chunk_bytes: int | None = None,
            timeout_sec: float = 300.0,  # 5 minutes timeout for large file transfers
    ) -> Dict[str, Any]:
        """
        Requests the drone to stream back the specified recording files.
        Files are sent in chunks to avoid overwhelming the WebSocket connection.
        """
        if ws is None:
            raise NoDroneConnectedError("No drone is connected.")

        requested_names = [name for name in names if isinstance(name, str) and name.strip()]
        if not requested_names:
            raise ValueError("No valid recording names provided.")

        # Fallback to config defaults if not explicitly provided
        batch = int(batch_size) if batch_size is not None else int(self.config.pull_batch_size)
        chunk = int(chunk_bytes) if chunk_bytes is not None else int(self.config.pull_chunk_bytes)

        # Enforce sane limits to prevent memory exhaustion
        batch = max(1, min(batch, 32))
        chunk = max(64 * 1024, min(chunk, 2 * 1024 * 1024))  # Between 64KB and 2MB per chunk

        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        previous = self.recordings_ack_waiters.get("PULL_RECORDINGS")
        if previous is not None and not previous.done():
            previous.cancel()
        self.recordings_ack_waiters["PULL_RECORDINGS"] = waiter

        payload = {
            "type": "RECORDINGS",
            "action": "PULL_RECORDINGS",
            "names": requested_names,
            "batch_size": batch,
            "chunk_bytes": chunk,
        }

        try:
            await ws.send(json.dumps(payload))
            logger.debug(
                f"[WS] PULL_RECORDINGS sent to drone "
                f"(files={len(requested_names)}, batch_size={batch}, chunk_bytes={chunk})."
            )
        except Exception as e:
            self._cancel_waiter(self.recordings_ack_waiters, "PULL_RECORDINGS")
            logger.error(f"[WS] send failed: {e}")
            raise DroneCommandFailedError("Failed to send PULL_RECORDINGS to the drone") from e

        try:
            ack = await asyncio.wait_for(waiter, timeout=timeout_sec)
        except asyncio.TimeoutError as exc:
            self._cancel_waiter(self.recordings_ack_waiters, "PULL_RECORDINGS")
            raise DroneCommandFailedError(
                "Timed out waiting for PULL_RECORDINGS ACK from drone."
            ) from exc

        # Once the transfer finishes, we finalize the files (e.g., format conversion)
        transfer_id = ack.get("transfer_id")
        processed_results = await self._finalize_pull_transfer(transfer_id=str(transfer_id) if transfer_id else None)
        ack["processed_results"] = processed_results

        return ack

    ''' ---------- HELPER METHODS ----------'''

    @staticmethod
    def format_disconnect_reason(exc: websockets.ConnectionClosed) -> str:
        """Parses the websocket close exception into a human-readable string."""
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
            # No close frame was exchanged, likely a sudden network drop
            code = CloseCode.ABNORMAL_CLOSURE

        details = str(exc)

        if code is None:
            return f"details={details}"
        if reason:
            return f"code={code}, reason={reason}, details={details}"
        return f"code={code}, details={details}"

    def _cancel_waiter(self, waiter_dict: Dict[str, asyncio.Future], key: str):
        """Safely removes and cancels a pending future."""
        waiter = waiter_dict.pop(key, None)
        if waiter is not None and not waiter.done():
            waiter.cancel()

    def handle_recording_ack(self, ack: Dict[str, Any]):
        """Resolves the future waiting for a RECORDING action ACK."""
        action = ack.get("action")
        ok = ack.get("ok")
        logger.debug(
            f"[ACK ← RPi] RECORDING action={action} ok={ok} "
            f"recording={ack.get('recording')} ref_count={ack.get('ref_count')} "
            f"path={ack.get('path')} err={ack.get('error')}"
        )
        waiter = self.recording_ack_waiters.pop(str(action), None)
        if waiter is not None and not waiter.done():
            waiter.set_result(ack)

    def handle_recordings_ack(self, ack: Dict[str, Any]):
        """Resolves the future waiting for a RECORDINGS action ACK."""
        action = ack.get("action")
        ok = ack.get("ok")
        logger.debug(
            f"[ACK ← RPi] RECORDINGS action={action} ok={ok} "
            f"count={ack.get('count')} completed={ack.get('completed_count')} "
            f"err={ack.get('error')}"
        )
        waiter = self.recordings_ack_waiters.pop(str(action), None)
        if waiter is not None and not waiter.done():
            waiter.set_result(ack)

    def clear_waiters(self, waiters: Dict[str, asyncio.Future], reason: str):
        """Rejects all pending futures when the connection is unexpectedly lost."""
        for key, waiter in list(waiters.items()):
            if not waiter.done():
                waiter.set_exception(
                    DroneConnectionLostError(f"{reason} for {key}.")
                )
        waiters.clear()

    async def handle_recording_file_begin(self, *, transfer_id: str, name: str, payload: dict[str, Any]) -> None:
        """Initializes state and opens a temporary file handle for an incoming video transfer."""
        transfer = self._pull_transfers.setdefault(
            transfer_id,
            {"active_files": {}, "completed": {}, "receive_errors": {}},
        )
        active_files = transfer["active_files"]
        completed = transfer["completed"]

        safe_name = Path(name).name
        raw_path = Path(self.config.recordings_raw_dir) / safe_name
        tmp_path = raw_path.with_suffix(raw_path.suffix + ".part")

        # Prep the destination file
        try:
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            if tmp_path.exists():
                tmp_path.unlink()
            fh = tmp_path.open("wb")
        except Exception as exc:
            transfer["receive_errors"][safe_name] = f"begin_failed: {exc}"
            logger.error(f"[WS] begin receive failed for {safe_name}: {exc}")
            return

        # Save metadata alongside the file if provided
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

        # Ensure we don't have stale completed data for this file
        completed.pop(safe_name, None)

    async def handle_recording_file_chunk(
            self,
            *,
            transfer_id: str,
            name: str,
            seq: int,
            chunk_b64: str,
    ) -> None:
        """Decodes and appends an incoming base64 chunk to the temporary file."""
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

            # Update transfer progress
            state["bytes_received"] = int(state.get("bytes_received", 0)) + len(chunk)
            state["chunks_received"] = max(int(state.get("chunks_received", 0)), seq + 1)
        except Exception as exc:
            transfer["receive_errors"][safe_name] = f"chunk_failed: {exc}"
            logger.warning(f"[WS] chunk receive failed for {safe_name}: {exc}")

    async def handle_recording_file_end(
            self,
            *,
            transfer_id: str,
            name: str,
            payload: dict[str, Any],
    ) -> None:
        """Closes the file handle and renames the temporary file to its final raw path."""
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
                pass  # Already closed or broken, nothing we can do

        tmp_path = state.get("tmp_path")
        raw_path = state.get("raw_path")

        try:
            if isinstance(tmp_path, Path) and isinstance(raw_path, Path):
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path.replace(raw_path)  # Remove the .part extension
        except Exception as exc:
            transfer["receive_errors"][safe_name] = f"finalize_failed: {exc}"
            logger.error(f"[WS] finalize receive failed for {safe_name}: {exc}")
            return

        # Mark the file as successfully transferred
        transfer["completed"][safe_name] = {
            "raw_path": str(raw_path) if isinstance(raw_path, Path) else None,
            "metadata": state.get("metadata"),
            "metadata_path": str(state.get("metadata_path")) if isinstance(state.get("metadata_path"), Path) else None,
            "bytes_received": int(state.get("bytes_received", 0)),
            "chunks_received": int(state.get("chunks_received", 0)),
            "expected_chunks": int(payload.get("chunks", 0)),
            "size_bytes_expected": int(state.get("size_bytes_expected", 0)),
        }

    def cleanup_pull_transfers(self):
        """Closes any dangling file handles from interrupted transfers to avoid file locks."""
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
                        pass
        self._pull_transfers.clear()

    async def _finalize_pull_transfer(self, *, transfer_id: str | None) -> list[dict[str, Any]]:
        """Processes all files from a completed transfer batch (e.g., runs format conversions)."""
        if not transfer_id:
            return []

        transfer = self._pull_transfers.pop(transfer_id, None)
        if not isinstance(transfer, dict):
            return []

        completed = transfer.get("completed", {})
        receive_errors = transfer.get("receive_errors", {})

        if not isinstance(completed, dict): completed = {}
        if not isinstance(receive_errors, dict): receive_errors = {}

        results: list[dict[str, Any]] = []
        names = set(completed.keys()) | set(receive_errors.keys())

        for name in sorted(names):
            summary = await self._process_pulled_file(name, completed, receive_errors)
            results.append(summary)

        return results

    async def _process_pulled_file(self, name: str, completed: dict, receive_errors: dict) -> dict[str, Any]:
        """Validates a downloaded file and triggers ffmpeg conversion to MP4."""
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

        # Check for transfer errors first
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

        # Kick off the conversion process
        try:
            conversion = await self._convert_raw_recording(raw_path=raw_path, metadata=metadata)
            summary.update(conversion)
        except Exception as e:
            summary["convert_ok"] = False
            summary["convert_error"] = f"Conversion process failed: {e}"

        return summary

    async def _convert_raw_recording(self, *, raw_path: Path, metadata: dict[str, Any] | None) -> dict[str, Any]:
        """Spawns a ffmpeg process in a separate thread so it doesn't block the asyncio event loop."""
        mp4_path = Path(self.config.recordings_mp4_dir) / f"{raw_path.stem}.mp4"
        fps = self._resolve_recording_fps(metadata)

        # Run the synchronous subprocess call in a thread pool
        result = await asyncio.to_thread(self._run_ffmpeg_conversion, raw_path, mp4_path, fps)

        result["mp4_path"] = str(mp4_path)
        result["fps_used"] = fps
        return result

    @staticmethod
    def _run_ffmpeg_conversion(raw_path: Path, mp4_path: Path, fps: int) -> dict[str, Any]:
        """
        Executes the actual ffmpeg shell commands.
        It first tries to fast-copy (remux) the video stream. If the raw format
        doesn't support this, it falls back to a full re-encode (h264).
        """
        mp4_path.parent.mkdir(parents=True, exist_ok=True)

        # Attempt 1: Fast remux (just changing the container to mp4, no CPU heavy encoding)
        remux_cmd = [
            "ffmpeg",
            "-y",  # Overwrite output
            "-framerate", str(fps),
            "-i", str(raw_path),
            "-c", "copy",  # Copy the raw video stream directly
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

        # Attempt 2: If copying fails (often due to raw h264 streams lacking container info),
        # fallback to a full re-encode.
        reencode_cmd = [
            "ffmpeg",
            "-y",
            "-framerate", str(fps),
            "-i", str(raw_path),
            "-c:v", "libx264",  # Re-encode to H.264
            "-pix_fmt", "yuv420p",
            str(mp4_path),
        ]

        reencode = subprocess.run(reencode_cmd, check=False, capture_output=True, text=True)

        if reencode.returncode == 0:
            return {"convert_ok": True, "convert_mode": "reencode"}

        # Both methods failed, capture the error output for debugging
        stderr = (reencode.stderr or "").strip()
        if not stderr:
            stderr = (remux.stderr or "").strip()

        return {"convert_ok": False, "convert_error": stderr or "ffmpeg_failed"}

    def _resolve_recording_fps(self, metadata: dict[str, Any] | None) -> int:
        """Extracts the framerate from metadata, falling back to a config default if missing or invalid."""
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