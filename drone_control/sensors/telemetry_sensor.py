from typing import Any, Callable

from drone_control.sensors.mavlink_telemetry_backend import get_telemetry_json
from drone_control.sensors.base import Sensor


class TelemetrySensor(Sensor):
    name = "telemetry"

    def __init__(self, *, mav_device: str, mav_baud: int, timeout: float):
        self.mav_device = mav_device
        self.mav_baud = mav_baud
        self.timeout = timeout

        self._reader: Callable[..., dict[str, Any]] | None = None
        try:
            self._reader = get_telemetry_json
        except Exception as exc:
            self._reader = None
            print(f"[RPi] WARN: pixhawk_telemetry not available: {exc} - will send empty telemetry.")

    def health(self) -> dict[str, object]:
        return {
            "sensor": self.name,
            "reader_available": self._reader is not None,
            "mav_device": self.mav_device,
            "mav_baud": self.mav_baud,
            "timeout": self.timeout,
        }

    def snapshot(self) -> dict[str, Any]:
        if self._reader is None:
            return {}

        try:
            data = self._reader(
                device=self.mav_device,
                baud=self.mav_baud,
                wait_for_data=True,
                timeout=self.timeout,
            )
        except Exception as exc:
            print(f"[RPi] TELEMETRY read error: {exc}")
            data = None

        if not data:
            return {}

        try:
            alt = (data.get("position") or {}).get("alt")
            if alt is not None and "height" not in data:
                data["height"] = alt
        except Exception:
            pass

        return data
