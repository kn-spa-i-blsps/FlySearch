import json
from dataclasses import dataclass
from typing import Any

IN_COMMAND = "COMMAND"


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
            return InboundMessage(kind=IN_COMMAND, raw=message, json_obj=obj)

        return InboundMessage(kind="JSON", raw=message, json_obj=obj if isinstance(obj, dict) else None)

    return InboundMessage(kind="NON_TEXT", raw=message)
