from drone_control.actuators.base import Actuator
from drone_control.actuators.pixhawk_vector_backend import send_vector_command
from drone_control.utils.coords import grid_xyz_to_ned


class FlightController(Actuator):
    name = "flight_controller"

    def __init__(self, *, exec_moves: bool, move_method: int, mav_device: str, mav_baud: int):
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

    def maybe_execute_move(self, move: tuple[float, float, float]) -> bool:
        if not self.exec_moves:
            print("[RPi] EXECUTE_MOVES=0 -> command logged only, no FC execute.")
            return False

        try:
            ned = grid_xyz_to_ned(move)
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
