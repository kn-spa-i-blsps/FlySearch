import json
from typing import Any, Optional

from drone_control.protocols.outbound import build_command_ack
from drone_control.command_registry.command_descriptors import CommandDescriptor


class CommandRegistry:
    """Maps command actions to descriptors and owns the dispatch logic.
    Written once; never touched when adding new commands.
    """

    def __init__(self):
        self._descriptors: dict[str, CommandDescriptor] = {}

    def register(self, descriptor: CommandDescriptor) -> None:
        self._descriptors[descriptor.action] = descriptor

    def get(self, action: str) -> Optional[CommandDescriptor]:
        return self._descriptors.get(action)

    def actions(self) -> list[str]:
        return list(self._descriptors.keys())

    def dispatch(self, ws: Any, action: str, seq: Optional[int]) -> bool:
        """Try to handle the action. Returns True if handled, False if unknown.

        For registered actions:
        1. Optionally sends an immediate generic ACK (send_immediate_ack=True).
        2. Calls the handler to acquire data.
        3. Sends the built response.
        If the handler raises and build_error_response is provided, sends an error response.
        """
        descriptor = self.get(action)
        if descriptor is None:
            return False

        if descriptor.send_immediate_ack:
            ws.send(json.dumps(build_command_ack(seq=seq, ok=True, action=action)))

        try:
            data = descriptor.handler()
            response = descriptor.build_response(data, seq)
            ws.send(json.dumps(response))
            print(f"[RPi] {action} response sent (seq={seq})")
        except Exception as exc:
            print(f"[RPi] {action} error: {exc}")
            if descriptor.build_error_response is not None:
                try:
                    ws.send(json.dumps(descriptor.build_error_response(exc, seq)))
                except Exception:
                    pass

        return True
