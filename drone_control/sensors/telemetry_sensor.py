import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from drone_control.sensors.mavlink_telemetry_backend import get_telemetry_json
from drone_control.sensors.base import Sensor


class TelemetrySensor(Sensor):
    name = "telemetry"

    def __init__(
        self,
        *,
        mav_device: str,
        mav_baud: int,
        timeout: float,
        telemetry_template_path: str | Path | None = None,
    ):
        self.mav_device = mav_device
        self.mav_baud = mav_baud
        self.timeout = timeout
        project_root = Path(__file__).resolve().parents[2]
        raw_template_path = Path(telemetry_template_path) if telemetry_template_path else Path("telemetry.json")
        self.telemetry_template_path = (
            raw_template_path if raw_template_path.is_absolute() else project_root / raw_template_path
        )
        self._fallback_template = self._load_fallback_template()

        self._reader: Callable[..., dict[str, Any]] | None = None
        self._reader_unavailable_reason: str | None = None
        try:
            self._reader = get_telemetry_json
        except Exception as exc:
            self._reader = None
            self._reader_unavailable_reason = str(exc)
            print(
                f"[RPi] WARN: pixhawk_telemetry not available: {exc} - "
                "will send fallback empty telemetry template."
            )

    def _load_fallback_template(self) -> dict[str, Any]:
        try:
            with self.telemetry_template_path.open("r", encoding="utf-8") as file_obj:
                loaded = json.load(file_obj)
            if isinstance(loaded, dict):
                return loaded
            print(
                f"[RPi] TELEMETRY template at {self.telemetry_template_path} is not a JSON object; "
                "falling back to {}."
            )
            return {}
        except Exception as exc:
            print(
                f"[RPi] TELEMETRY template load error ({self.telemetry_template_path}): {exc}; "
                "falling back to {}."
            )
            return {}

    def _fallback_template_with_reason(self, reason: str) -> dict[str, Any]:
        print(f"[RPi] TELEMETRY fallback: {reason}; sending template from {self.telemetry_template_path}.")
        return deepcopy(self._fallback_template)

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
            reason = self._reader_unavailable_reason or "telemetry backend is unavailable"
            return self._fallback_template_with_reason(f"unavailable ({reason})")

        try:
            data = self._reader(
                device=self.mav_device,
                baud=self.mav_baud,
                wait_for_data=True,
                timeout=self.timeout,
            )
        except Exception as exc:
            return self._fallback_template_with_reason(f"read error ({exc})")

        if not data:
            return self._fallback_template_with_reason("reader returned no data")

        try:
            alt = (data.get("position") or {}).get("alt")
            if alt is not None and "height" not in data:
                data["height"] = alt
        except Exception:
            pass

        return data
