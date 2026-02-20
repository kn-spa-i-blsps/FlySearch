import base64
import json
from pathlib import Path
from typing import Any

from drone_control.protocols.outbound import build_photo_with_telemetry_payload
from drone_control.sensors.photo_sensor import PhotoSensor
from drone_control.sensors.telemetry_sensor import TelemetrySensor


class AcquisitionManager:
    def __init__(self, *, photo_sensor: PhotoSensor, telemetry_sensor: TelemetrySensor):
        self.photo_sensor = photo_sensor
        self.telemetry_sensor = telemetry_sensor

    def capture_photo_bytes(self) -> bytes:
        return self.photo_sensor.capture_bytes()

    def load_telemetry_template(self, path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as file_obj:
                return json.load(file_obj)
        except FileNotFoundError:
            print(f"[RPi] {path} not found - sending empty {{}}")
            return {}

    def build_photo_with_telemetry(self) -> dict[str, Any]:
        photo_base64 = None
        try:
            photo_data = self.photo_sensor.capture_bytes()
            photo_base64 = base64.b64encode(photo_data).decode("utf-8")
        except Exception as exc:
            print(f"[RPi] PHOTO_WITH_TELEMETRY: photo error: {exc}")

        telemetry = self.telemetry_sensor.snapshot()
        return build_photo_with_telemetry_payload(photo_base64=photo_base64, telemetry=telemetry)
