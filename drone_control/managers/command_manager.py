from typing import Any

from drone_control.actuators.flight_controller import FlightController
from drone_control.managers.session_log_manager import SessionLogManager
from drone_control.protocols.outbound import build_command_ack


class CommandManager:
    """Handles incoming control commands from mission side and triggers actuator execution."""
    def __init__(self, *, logger: SessionLogManager, flight_controller: FlightController):
        self.logger = logger
        self.flight_controller = flight_controller

    def handle_command(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        # Echo the server's seq back in the ACK so the server can correlate responses.
        server_seq = payload.get("seq")
        action = payload.get("action")
        log_seq = None
        try:
            log_seq = self.logger.next_seq()

            executed = False
            if action == "FOUND":
                self.logger.store_found(log_seq)
                print("[RPi] COMMAND received: FOUND")
            elif "move" in payload:
                x, y, z = payload["move"]
                move = (float(x), float(y), float(z))
                print(f"[RPi] COMMAND received: MOVE (x={x}, y={y}, z={z})")
                executed = self.flight_controller.maybe_execute_move(move)
                self.logger.store_move(log_seq, move)
            else:
                print(f"[RPi] Unknown COMMAND payload: {payload}")
                return None

            print(
                "[RPi] COMMAND stored "
                f"(seq={log_seq}) -> {self.logger.runtime_context.session_file.name}; "
                "latest_command.json updated"
            )
            return build_command_ack(seq=server_seq, ok=True, action=action, executed=executed)
        except Exception as exc:
            print(f"[RPi] COMMAND store error: {exc}")
            return build_command_ack(seq=server_seq, ok=False, action=action, error=str(exc))
