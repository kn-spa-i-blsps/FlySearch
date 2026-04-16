import json
from typing import Any

import websocket

from drone_control.command_registry import CommandRegistry
from drone_control.command_registry.drone_commands import pull_recordings
from drone_control.managers.command_manager import CommandManager
from drone_control.protocols.inbound import IN_COMMAND, parse_inbound_message
from drone_control.protocols.outbound import build_command_ack, invalid_message_response
from drone_control.sensors.recording_sensor import RecordingSensor


class MessageRouter:
    """Dispatcher for incoming WS messages."""
    def __init__(
        self,
        *,
        command_manager: CommandManager,
        command_registry: CommandRegistry,
        recording_sensor: RecordingSensor,
    ):
        self.command_manager = command_manager
        self.command_registry = command_registry
        self.recording_sensor = recording_sensor

    def on_message(self, ws: websocket.WebSocketApp, message: Any) -> None:
        preview = message if isinstance(message, str) else f"<{len(message)} bytes>"
        if isinstance(preview, str) and len(preview) > 160:
            preview = preview[:160] + "..."
        print(f"Received: {preview}")

        parsed = parse_inbound_message(message)

        if parsed.kind == IN_COMMAND and parsed.json_obj is not None:
            obj = parsed.json_obj
            action = obj.get("action")
            seq = obj.get("seq")

            if self.command_registry.dispatch(ws, action, seq):
                return

            if action == "PULL_RECORDINGS":
                pull_recordings(ws, obj, self.recording_sensor)
                return

            # MOVE — two-phase: immediate ACK then MOVE_EXECUTED after execution.
            if action == "MOVE":
                ws.send(json.dumps(build_command_ack(seq=seq, ok=True, action="MOVE")))
                print(f"[RPi] MOVE immediate ACK sent (seq={seq})")
                move_ok = False
                try:
                    result = self.command_manager.handle_command(obj)
                    move_ok = bool(result.get("ok", False)) if result else False
                except Exception as exc:
                    print(f"[RPi] MOVE execution error: {exc}")
                ws.send(json.dumps({"type": "MOVE_EXECUTED", "seq": seq, "ok": move_ok}))
                print(f"[RPi] MOVE_EXECUTED sent (seq={seq}, ok={move_ok})")
                return

            # All other commands (e.g. FOUND) via command_manager.
            ack = self.command_manager.handle_command(obj)
            if ack is None:
                return
            try:
                ws.send(json.dumps(ack))
                print(f"[RPi] ACK sent (seq={ack.get('seq')})")
            except Exception:
                pass
            return

        # Server ACKs — log and ignore.
        if parsed.kind == "JSON" and parsed.json_obj is not None and parsed.json_obj.get("type") == "ACK":
            obj = parsed.json_obj
            print(f"[RPi] Server ACK received (of={obj.get('of')}, seq={obj.get('seq')}, ok={obj.get('ok')})")
            return

        if isinstance(message, str):
            print(f"[RPi] Unrecognized TEXT (not a command): {message[:200]}")
        else:
            print(f"[RPi] Unrecognized NON-TEXT message (len={len(message)})")

        ws.send(invalid_message_response())
