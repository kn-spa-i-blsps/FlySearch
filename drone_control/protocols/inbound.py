import json
from dataclasses import dataclass
from typing import Any

IN_SEND_PHOTO = "SEND_PHOTO"
IN_TELEMETRY = "TELEMETRY"
IN_PHOTO_WITH_TELEMETRY = "PHOTO_WITH_TELEMETRY"
IN_COMMAND = "COMMAND"
IN_START_RECORDING = "START_RECORDING"
IN_STOP_RECORDING = "STOP_RECORDING"
IN_GET_RECORDINGS = "GET_RECORDINGS"


@dataclass
class InboundMessage:
    """Normalized incoming WS message."""
    kind: str
    raw: Any
    json_obj: dict[str, Any] | None = None


def parse_inbound_message(message: Any) -> InboundMessage:
    if isinstance(message, str):
        if message == IN_SEND_PHOTO:
            return InboundMessage(kind=IN_SEND_PHOTO, raw=message)
        if message == IN_TELEMETRY:
            return InboundMessage(kind=IN_TELEMETRY, raw=message)
        if message == IN_PHOTO_WITH_TELEMETRY:
            return InboundMessage(kind=IN_PHOTO_WITH_TELEMETRY, raw=message)
        if message == IN_START_RECORDING:
            return InboundMessage(kind=IN_START_RECORDING, raw=message)
        if message == IN_STOP_RECORDING:
            return InboundMessage(kind=IN_STOP_RECORDING, raw=message)
        if message == IN_GET_RECORDINGS:
            return InboundMessage(kind=IN_GET_RECORDINGS, raw=message)

        try:
            obj = json.loads(message)
        except Exception:
            return InboundMessage(kind="TEXT", raw=message)

        if isinstance(obj, dict) and obj.get("type") == IN_COMMAND:
            return InboundMessage(kind=IN_COMMAND, raw=message, json_obj=obj)

        return InboundMessage(kind="JSON", raw=message, json_obj=obj if isinstance(obj, dict) else None)

    return InboundMessage(kind="NON_TEXT", raw=message)
