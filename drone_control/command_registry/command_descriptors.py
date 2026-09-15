from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandDescriptor:
    """Describes a command: how to acquire data and how to build the response.

    send_immediate_ack: if True, a generic COMMAND ACK is sent before the handler runs.
        Use True for data acquisition (GET_PHOTO_TELEMETRY) where the client waits for two
        messages. Use False when the response itself is the ACK (recording commands).

    build_error_response: optional builder called when handler raises. If None, errors are
        only logged. Provide this for commands where the client expects a response even on
        failure (e.g. recording commands).
    """

    action: str
    handler: Callable[[], Any]
    build_response: Callable[[Any, int | None], dict[str, Any]]
    send_immediate_ack: bool = True
    build_error_response: Callable[[Exception, int | None], dict[str, Any]] | None = (
        field(default=None)
    )
