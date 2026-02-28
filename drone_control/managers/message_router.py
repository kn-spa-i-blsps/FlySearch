import json
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
    parse_inbound_message
)
from drone_control.protocols.outbound import (
    build_telemetry_payload,
    invalid_message_response,
)


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
