import json
from dataclasses import dataclass
from typing import Any

IN_SEND_PHOTO = "SEND_PHOTO"
IN_TELEMETRY = "TELEMETRY"
IN_PHOTO_WITH_TELEMETRY = "PHOTO_WITH_TELEMETRY"
IN_COMMAND = "COMMAND"


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

        try:
            obj = json.loads(message)
        except Exception:
            return InboundMessage(kind="TEXT", raw=message)

        if isinstance(obj, dict) and obj.get("type") == IN_COMMAND:
            return InboundMessage(kind=IN_COMMAND, raw=message, json_obj=obj)

        return InboundMessage(kind="JSON", raw=message, json_obj=obj if isinstance(obj, dict) else None)

    return InboundMessage(kind="NON_TEXT", raw=message)
