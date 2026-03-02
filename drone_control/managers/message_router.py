import base64
import json
from pathlib import Path
from typing import Any

import websocket

from drone_control.managers.acquisition_manager import AcquisitionManager
from drone_control.managers.command_manager import CommandManager
from drone_control.protocols.inbound import (
    IN_COMMAND,
    IN_PHOTO_WITH_TELEMETRY,
    IN_SEND_PHOTO,
    IN_TELEMETRY,
    IN_START_RECORDING,
    IN_STOP_RECORDING,
    IN_GET_RECORDINGS,
    IN_PULL_RECORDINGS,
    parse_inbound_message
)
from drone_control.protocols.outbound import (
    build_telemetry_payload,
    invalid_message_response,
)
from drone_control.utils.time import now_ts


class MessageRouter:
    """Dispatcher for incoming WS messages"""
    def __init__(
        self,
        *,
        acquisition: AcquisitionManager,
        command_manager: CommandManager
    ):
        self.acquisition = acquisition
        self.command_manager = command_manager

    def on_message(self, ws: websocket.WebSocketApp, message: Any) -> None:
        preview = message if isinstance(message, str) else f"<{len(message)} bytes>"
        if isinstance(preview, str) and len(preview) > 160:
            preview = preview[:160] + "..."
        print(f"Received: {preview}")

        parsed = parse_inbound_message(message)

        if parsed.kind == IN_SEND_PHOTO:
            photo_data = self.acquisition.capture_photo_bytes()
            ws.send(photo_data, opcode=websocket.ABNF.OPCODE_BINARY)
            print("Sent photo bytes")
            return

        if parsed.kind == IN_TELEMETRY:
            telemetry = self.acquisition.capture_telemetry()
            ws.send(json.dumps(build_telemetry_payload(telemetry)))
            print("[RPi] Sent TELEMETRY json")
            return

        if parsed.kind == IN_PHOTO_WITH_TELEMETRY:
            payload = self.acquisition.build_photo_with_telemetry()
            ws.send(json.dumps(payload))
            telem_keys = list((payload.get("telemetry") or {}).keys())
            print(
                "[RPi] Sent PHOTO_WITH_TELEMETRY "
                f"(photo={payload.get('photo') is not None}, telem_keys={telem_keys})"
            )
            return

        if parsed.kind == IN_START_RECORDING:
            try:
                status = self.acquisition.start_recording()
                ack = {
                    "type": "ACK",
                    "of": "RECORDING",
                    "action": IN_START_RECORDING,
                    "ok": bool(status.get("ok", True)),
                    "recording": bool(status.get("recording", False)),
                    "ref_count": int(status.get("ref_count", 0)),
                    "path": status.get("path"),
                    "metadata_path": status.get("metadata_path"),
                    "metadata": status.get("metadata"),
                }
                print(f"[RPi] START_RECORDING status={status}")
            except Exception as exc:
                ack = {
                    "type": "ACK",
                    "of": "RECORDING",
                    "action": IN_START_RECORDING,
                    "ok": False,
                    "error": str(exc),
                }
                print(f"[RPi] START_RECORDING error: {exc}")
            try:
                ws.send(json.dumps(ack))
            except Exception:
                pass
            return

        if parsed.kind == IN_STOP_RECORDING:
            try:
                status = self.acquisition.stop_recording()
                ack = {
                    "type": "ACK",
                    "of": "RECORDING",
                    "action": IN_STOP_RECORDING,
                    "ok": bool(status.get("ok", True)),
                    "recording": bool(status.get("recording", False)),
                    "ref_count": int(status.get("ref_count", 0)),
                    "path": status.get("path"),
                    "metadata_path": status.get("metadata_path"),
                    "metadata": status.get("metadata"),
                }
                print(f"[RPi] STOP_RECORDING status={status}")
            except Exception as exc:
                ack = {
                    "type": "ACK",
                    "of": "RECORDING",
                    "action": IN_STOP_RECORDING,
                    "ok": False,
                    "error": str(exc),
                }
                print(f"[RPi] STOP_RECORDING error: {exc}")
            try:
                ws.send(json.dumps(ack))
            except Exception:
                pass
            return

        if parsed.kind == IN_GET_RECORDINGS:
            try:
                recordings = self.acquisition.list_recordings()
                ack = {
                    "type": "ACK",
                    "of": "RECORDINGS",
                    "action": IN_GET_RECORDINGS,
                    "ok": True,
                    "count": len(recordings),
                    "recordings": recordings,
                }
                print(f"[RPi] GET_RECORDINGS count={len(recordings)}")
            except Exception as exc:
                ack = {
                    "type": "ACK",
                    "of": "RECORDINGS",
                    "action": IN_GET_RECORDINGS,
                    "ok": False,
                    "error": str(exc),
                    "count": 0,
                    "recordings": [],
                }
                print(f"[RPi] GET_RECORDINGS error: {exc}")
            try:
                ws.send(json.dumps(ack))
            except Exception:
                pass
            return

        if parsed.kind == IN_PULL_RECORDINGS:
            payload = parsed.json_obj or {}
            names_raw = payload.get("names", [])
            names: list[str] = [name for name in names_raw if isinstance(name, str)] if isinstance(names_raw, list) else []

            if not names:
                ack = {
                    "type": "ACK",
                    "of": "RECORDINGS",
                    "action": IN_PULL_RECORDINGS,
                    "ok": False,
                    "error": "No recording names provided.",
                    "requested_count": 0,
                    "completed_count": 0,
                    "results": [],
                }
                try:
                    ws.send(json.dumps(ack))
                except Exception:
                    pass
                return

            batch_size_raw = payload.get("batch_size", 2)
            chunk_bytes_raw = payload.get("chunk_bytes", 512 * 1024)
            try:
                batch_size = max(1, min(int(batch_size_raw), 32))
            except Exception:
                batch_size = 2
            try:
                chunk_bytes = max(64 * 1024, min(int(chunk_bytes_raw), 2 * 1024 * 1024))
            except Exception:
                chunk_bytes = 512 * 1024

            transfer_id = f"pull_{now_ts()}"
            prepared, rejected = self.acquisition.prepare_recordings_for_pull(names)

            results: list[dict[str, object]] = []
            for item in rejected:
                results.append({
                    "name": item.get("name"),
                    "ok": False,
                    "error": item.get("error", "rejected"),
                })

            for idx in range(0, len(prepared), batch_size):
                batch = prepared[idx:idx + batch_size]
                for entry in batch:
                    name = str(entry.get("name"))
                    path = Path(str(entry.get("path")))
                    size_bytes = int(entry.get("size_bytes", 0))
                    metadata = entry.get("metadata")
                    metadata_obj = metadata if isinstance(metadata, dict) else None
                    metadata_exists = bool(entry.get("metadata_exists", False))

                    begin_payload = {
                        "type": "RECORDING_FILE_BEGIN",
                        "transfer_id": transfer_id,
                        "name": name,
                        "size_bytes": size_bytes,
                        "metadata_exists": metadata_exists,
                        "metadata": metadata_obj,
                    }
                    try:
                        ws.send(json.dumps(begin_payload))
                    except Exception as exc:
                        results.append({
                            "name": name,
                            "ok": False,
                            "error": f"begin_send_failed: {exc}",
                        })
                        continue

                    chunks = 0
                    try:
                        with path.open("rb") as file_obj:
                            while True:
                                chunk = file_obj.read(chunk_bytes)
                                if not chunk:
                                    break
                                ws.send(json.dumps({
                                    "type": "RECORDING_FILE_CHUNK",
                                    "transfer_id": transfer_id,
                                    "name": name,
                                    "seq": chunks,
                                    "data": base64.b64encode(chunk).decode("ascii"),
                                }))
                                chunks += 1

                        ws.send(json.dumps({
                            "type": "RECORDING_FILE_END",
                            "transfer_id": transfer_id,
                            "name": name,
                            "chunks": chunks,
                        }))
                        results.append({
                            "name": name,
                            "ok": True,
                            "size_bytes": size_bytes,
                            "chunks": chunks,
                            "metadata_exists": metadata_exists,
                        })
                    except Exception as exc:
                        results.append({
                            "name": name,
                            "ok": False,
                            "size_bytes": size_bytes,
                            "error": str(exc),
                        })

            completed_count = sum(1 for item in results if bool(item.get("ok", False)))
            ack = {
                "type": "ACK",
                "of": "RECORDINGS",
                "action": IN_PULL_RECORDINGS,
                "ok": completed_count == len(results) and len(results) > 0,
                "transfer_id": transfer_id,
                "requested_count": len(names),
                "completed_count": completed_count,
                "results": results,
                "batch_size": batch_size,
                "chunk_bytes": chunk_bytes,
            }
            print(
                "[RPi] PULL_RECORDINGS "
                f"requested={len(names)} completed={completed_count} "
                f"batch_size={batch_size} chunk_bytes={chunk_bytes}"
            )
            try:
                ws.send(json.dumps(ack))
            except Exception:
                pass
            return

        if parsed.kind == IN_COMMAND and parsed.json_obj is not None:
            ack = self.command_manager.handle_command(parsed.json_obj)
            if ack is None:
                return
            try:
                ws.send(json.dumps(ack))
                print(f"[RPi] ACK sent (seq={ack.get('seq')}, executed={ack.get('executed', False)})")
            except Exception:
                pass
            return

        if isinstance(message, str):
            print(f"[RPi] Unrecognized TEXT (not a command): {message[:200]}")
        else:
            print(f"[RPi] Unrecognized NON-TEXT message (len={len(message)})")

        ws.send(invalid_message_response())
