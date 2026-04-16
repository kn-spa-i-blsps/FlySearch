import json
from dataclasses import dataclass
from typing import Any

IN_COMMAND = "COMMAND"
IN_GET_PHOTO_TELEMETRY = "GET_PHOTO_TELEMETRY"
IN_START_RECORDING = "START_RECORDING"
IN_STOP_RECORDING = "STOP_RECORDING"
IN_GET_RECORDINGS = "GET_RECORDINGS"
IN_PULL_RECORDINGS = "PULL_RECORDINGS"


@dataclass
class InboundMessage:
    """Normalized incoming WS message."""
    kind: str
    raw: Any
    json_obj: dict[str, Any] | None = None


def parse_inbound_message(message: Any) -> InboundMessage:
    if isinstance(message, str):
        try:
            obj = json.loads(message)
        except Exception:
            return InboundMessage(kind="TEXT", raw=message)

        if isinstance(obj, dict) and obj.get("type") == IN_COMMAND:
            action = obj.get("action")
            if action == IN_GET_PHOTO_TELEMETRY:
                return InboundMessage(kind=IN_GET_PHOTO_TELEMETRY, raw=message, json_obj=obj)
            if action == IN_START_RECORDING:
                return InboundMessage(kind=IN_START_RECORDING, raw=message, json_obj=obj)
            if action == IN_STOP_RECORDING:
                return InboundMessage(kind=IN_STOP_RECORDING, raw=message, json_obj=obj)
            if action == IN_GET_RECORDINGS:
                return InboundMessage(kind=IN_GET_RECORDINGS, raw=message, json_obj=obj)
            if action == IN_PULL_RECORDINGS:
                return InboundMessage(kind=IN_PULL_RECORDINGS, raw=message, json_obj=obj)
            return InboundMessage(kind=IN_COMMAND, raw=message, json_obj=obj)

        return InboundMessage(kind="JSON", raw=message, json_obj=obj if isinstance(obj, dict) else None)

    return InboundMessage(kind="NON_TEXT", raw=message)
