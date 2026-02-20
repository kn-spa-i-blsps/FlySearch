from typing import Any

from drone_control.actuators.flight_controller import FlightController
from drone_control.managers.session_log_manager import SessionLogManager
from drone_control.protocols.outbound import build_command_ack


class CommandManager:
    def __init__(self, *, logger: SessionLogManager, flight_controller: FlightController):
        self.logger = logger
        self.flight_controller = flight_controller

    def handle_command(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        seq = None
        try:
            seq = self.logger.next_seq()

            executed = False
            if payload.get("action") == "FOUND":
                self.logger.store_found(seq)
                print("[RPi] COMMAND received: FOUND")
            elif "move" in payload:
                x, y, z = payload["move"]
                move = (float(x), float(y), float(z))
                print(f"[RPi] COMMAND received: MOVE (x={x}, y={y}, z={z})")
                executed = self.flight_controller.maybe_execute_move(move)
                self.logger.store_move(seq, move)
            else:
                print(f"[RPi] Unknown COMMAND payload: {payload}")
                return None

            print(
                "[RPi] COMMAND stored "
                f"(seq={seq}) -> {self.logger.runtime_context.session_file.name}; "
                "latest_command.json updated"
            )
            return build_command_ack(seq=seq, ok=True, executed=executed)
        except Exception as exc:
            print(f"[RPi] COMMAND store error: {exc}")
            return build_command_ack(seq=seq, ok=False, error=str(exc))
