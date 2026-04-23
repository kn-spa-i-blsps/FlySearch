import base64
import json
from pathlib import Path
from typing import Any, Optional

from drone_control.command_registry.command_descriptors import CommandDescriptor
from drone_control.command_registry.command_registry import CommandRegistry
from drone_control.sensors.photo_sensor import PhotoSensor
from drone_control.sensors.recording_sensor import RecordingSensor
from drone_control.sensors.telemetry_sensor import TelemetrySensor
from drone_control.utils.time import now_ts


def _recording_error_ack(action: str):
    def builder(exc: Exception, seq: Optional[int]) -> dict:
        return {"type": "ACK", "of": "RECORDING", "action": action, "ok": False, "error": str(exc)}
    return builder


def _capture_photo(photo_sensor: PhotoSensor) -> str | None:
    try:
        return base64.b64encode(photo_sensor.capture_bytes()).decode("utf-8")
    except Exception as exc:
        print(f"[RPi] PHOTO_WITH_TELEMETRY: photo error: {exc}")
        return None


def build_registry(
    *,
    photo_sensor: Optional[PhotoSensor] = None,
    telemetry_sensor: Optional[TelemetrySensor] = None,
    recording_sensor: Optional[RecordingSensor] = None,
) -> CommandRegistry:
    registry = CommandRegistry()

    if photo_sensor is not None:
        registry.register(CommandDescriptor(
            action="GET_PHOTO_TELEMETRY",
            handler=lambda: {
                "photo": _capture_photo(photo_sensor),
                "telemetry": telemetry_sensor.snapshot() if telemetry_sensor is not None else {},
            },
            build_response=lambda data, seq: {
                "type": "PHOTO_WITH_TELEMETRY",
                "photo": data.get("photo"),
                "telemetry": data.get("telemetry"),
                "seq": seq,
            },
            send_immediate_ack=True,
        ))

    if recording_sensor is not None:
        registry.register(CommandDescriptor(
            action="START_RECORDING",
            handler=recording_sensor.start_recording,
            build_response=lambda status, seq: {
                "type": "ACK", "of": "RECORDING", "action": "START_RECORDING",
                "ok": bool(status.get("ok", True)),
                "recording": bool(status.get("recording", False)),
                "ref_count": int(status.get("ref_count", 0)),
                "path": status.get("path"),
                "metadata_path": status.get("metadata_path"),
                "metadata": status.get("metadata"),
            },
            send_immediate_ack=False,
            build_error_response=_recording_error_ack("START_RECORDING"),
        ))

        registry.register(CommandDescriptor(
            action="STOP_RECORDING",
            handler=recording_sensor.stop_recording,
            build_response=lambda status, seq: {
                "type": "ACK", "of": "RECORDING", "action": "STOP_RECORDING",
                "ok": bool(status.get("ok", True)),
                "recording": bool(status.get("recording", False)),
                "ref_count": int(status.get("ref_count", 0)),
                "path": status.get("path"),
                "metadata_path": status.get("metadata_path"),
                "metadata": status.get("metadata"),
            },
            send_immediate_ack=False,
            build_error_response=_recording_error_ack("STOP_RECORDING"),
        ))

        registry.register(CommandDescriptor(
            action="GET_RECORDINGS",
            handler=recording_sensor.list_recordings,
            build_response=lambda recordings, seq: {
                "type": "ACK", "of": "RECORDINGS", "action": "GET_RECORDINGS",
                "ok": True,
                "count": len(recordings),
                "recordings": recordings,
            },
            send_immediate_ack=False,
            build_error_response=lambda exc, seq: {
                "type": "ACK", "of": "RECORDINGS", "action": "GET_RECORDINGS",
                "ok": False, "error": str(exc), "count": 0, "recordings": [],
            },
        ))

    return registry


def pull_recordings(ws: Any, obj: dict[str, Any], recording_sensor: RecordingSensor) -> None:
    names_raw = obj.get("names", [])
    names: list[str] = [n for n in names_raw if isinstance(n, str)] if isinstance(names_raw, list) else []

    if not names:
        try:
            ws.send(json.dumps({
                "type": "ACK", "of": "RECORDINGS", "action": "PULL_RECORDINGS",
                "ok": False, "error": "No recording names provided.",
                "requested_count": 0, "completed_count": 0, "results": [],
            }))
        except Exception:
            pass
        return

    batch_size_raw = obj.get("batch_size", 2)
    chunk_bytes_raw = obj.get("chunk_bytes", 512 * 1024)
    try:
        batch_size = max(1, min(int(batch_size_raw), 32))
    except Exception:
        batch_size = 2
    try:
        chunk_bytes = max(64 * 1024, min(int(chunk_bytes_raw), 2 * 1024 * 1024))
    except Exception:
        chunk_bytes = 512 * 1024

    transfer_id = f"pull_{now_ts()}"
    prepared, rejected = recording_sensor.prepare_recordings_for_pull(names)

    results: list[dict[str, object]] = []
    for item in rejected:
        results.append({"name": item.get("name"), "ok": False, "error": item.get("error", "rejected")})

    for idx in range(0, len(prepared), batch_size):
        for entry in prepared[idx:idx + batch_size]:
            name = str(entry.get("name"))
            path = Path(str(entry.get("path")))
            size_bytes = int(entry.get("size_bytes", 0))
            metadata = entry.get("metadata")
            metadata_obj = metadata if isinstance(metadata, dict) else None
            metadata_exists = bool(entry.get("metadata_exists", False))

            try:
                ws.send(json.dumps({
                    "type": "RECORDING_FILE_BEGIN", "transfer_id": transfer_id,
                    "name": name, "size_bytes": size_bytes,
                    "metadata_exists": metadata_exists, "metadata": metadata_obj,
                }))
            except Exception as exc:
                results.append({"name": name, "ok": False, "error": f"begin_send_failed: {exc}"})
                continue

            chunks = 0
            try:
                with path.open("rb") as file_obj:
                    while True:
                        chunk = file_obj.read(chunk_bytes)
                        if not chunk:
                            break
                        ws.send(json.dumps({
                            "type": "RECORDING_FILE_CHUNK", "transfer_id": transfer_id,
                            "name": name, "seq": chunks,
                            "data": base64.b64encode(chunk).decode("ascii"),
                        }))
                        chunks += 1
                ws.send(json.dumps({
                    "type": "RECORDING_FILE_END", "transfer_id": transfer_id,
                    "name": name, "chunks": chunks,
                }))
                results.append({"name": name, "ok": True, "size_bytes": size_bytes,
                                "chunks": chunks, "metadata_exists": metadata_exists})
            except Exception as exc:
                results.append({"name": name, "ok": False, "size_bytes": size_bytes, "error": str(exc)})

    completed_count = sum(1 for item in results if bool(item.get("ok", False)))
    print(
        f"[RPi] PULL_RECORDINGS requested={len(names)} completed={completed_count} "
        f"batch_size={batch_size} chunk_bytes={chunk_bytes}"
    )
    try:
        ws.send(json.dumps({
            "type": "ACK", "of": "RECORDINGS", "action": "PULL_RECORDINGS",
            "ok": completed_count == len(results) and len(results) > 0,
            "transfer_id": transfer_id, "requested_count": len(names),
            "completed_count": completed_count, "results": results,
            "batch_size": batch_size, "chunk_bytes": chunk_bytes,
        }))
    except Exception:
        pass
