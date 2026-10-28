from drone_control.actuators.pixhawk_vector_backend import send_vector_command
from drone_control.utils.coords import grid_xyz_to_ned
from drone_control.sensors.mavlink_telemetry_backend import get_telemetry_json


class FlightController:
    """Actuator-facing adapter between MOVE commands and Pixhawk MAVLink execution.
    Receives move vectors from CommandManager and decides whether to execute or log them.
    Written for the specific flight stack (Pixhawk/MAVLink)."""

    name = "flight_controller"

    def __init__(
        self, *, exec_moves: bool, move_method: int, mav_device: str, mav_baud: int
    ):
        self.exec_moves = exec_moves
        self.move_method = move_method
        self.mav_device = mav_device
        self.mav_baud = mav_baud
        self._sender = send_vector_command

    def health(self) -> dict[str, object]:
        return {
            "actuator": self.name,
            "enabled": self.exec_moves,
            "sender_available": True,
            "move_method": self.move_method,
        }
        
    def _get_current_altitude(self) -> float | None:
        telemetry = get_telemetry_json(
            device=self.mav_device,
            baud=self.mav_baud,
            wait_for_data=True,
            timeout=2.0,
        )

        if telemetry is None:
            return None

        position = telemetry.get("position", {})
        altitude = position.get("alt")

        return float(altitude) if altitude is not None else None 

    def maybe_execute_move(self, move: tuple[float, float, float], min_altitude: float) -> bool:
        """Main execution entrypoint for a move vector from upstream command handling"""
        if not self.exec_moves:
            print("[RPi] EXECUTE_MOVES=0 -> command logged only, no FC execute.")
            return False

        try:
            x, y, z = move
            current_altitude = self._get_current_altitude()
            
            if current_altitude is None:
                print("[RPi] Cannot verify altitude.")
                return False

            if current_altitude + z < min_altitude:
                z = min_altitude - current_altitude
                print(
                    f"[RPi] Adjusted move to respect minimum altitude: {min_altitude:.2f} m"
                )

            safe_move = (x, y, z)
            ned = grid_xyz_to_ned(safe_move)
            ok = self._sender(
                vector=ned,
                device=self.mav_device,
                baud=self.mav_baud,
                method_id=self.move_method,
            )
            print(f"[RPi] FC execute move ned={ned} method={self.move_method} ok={ok}")
            return bool(ok)
        except Exception as exc:
            print(f"[RPi] FC execute error: {exc}")
            return False
