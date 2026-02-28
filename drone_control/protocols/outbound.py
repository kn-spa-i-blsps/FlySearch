from typing import Any, Optional


def build_telemetry_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {"type": "TELEMETRY", "data": data}

def build_photo_with_telemetry_payload(
    *, photo_base64: Optional[str], telemetry: dict[str, Any]
) -> dict[str, Any]:
    return {"type": "PHOTO_WITH_TELEMETRY", "photo": photo_base64, "telemetry": telemetry}

def build_command_ack(
    *, seq: Optional[int], ok: bool, executed: bool = False, error: Optional[str] = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": "ACK", "of": "COMMAND", "ok": ok}
    if seq is not None:
        payload["seq"] = seq
    if ok:
        payload["executed"] = executed
    elif error is not None:
        payload["error"] = error
    return payload

def invalid_message_response() -> str:
    return "Message sent in invalid format. Accepted messages: 'SEND_PHOTO', 'TELEMETRY', 'PHOTO_WITH_TELEMETRY', 'START_RECORDING', 'STOP_RECORDING'"
