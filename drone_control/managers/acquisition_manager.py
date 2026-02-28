import base64
from typing import Any

from drone_control.protocols.outbound import build_photo_with_telemetry_payload
from drone_control.sensors import RecordingSensor
from drone_control.sensors.photo_sensor import PhotoSensor
from drone_control.sensors.telemetry_sensor import TelemetrySensor


class AcquisitionManager:
    """Handles sensor requests and workflows."""
    def __init__(self, *, photo_sensor: PhotoSensor, telemetry_sensor: TelemetrySensor, recording_sensor: RecordingSensor):
        self.photo_sensor = photo_sensor
        self.telemetry_sensor = telemetry_sensor
        self.recording_sensor = recording_sensor

    def capture_photo_bytes(self) -> bytes:
        return self.photo_sensor.capture_bytes()

    def capture_telemetry(self) -> dict[str, Any]:
        return self.telemetry_sensor.snapshot()

    def build_photo_with_telemetry(self) -> dict[str, Any]:
        photo_base64 = None
        try:
            photo_data = self.photo_sensor.capture_bytes()
            photo_base64 = base64.b64encode(photo_data).decode("utf-8")
        except Exception as exc:
            print(f"[RPi] PHOTO_WITH_TELEMETRY: photo error: {exc}")

        telemetry = self.telemetry_sensor.snapshot()
        return build_photo_with_telemetry_payload(photo_base64=photo_base64, telemetry=telemetry)

    def start_recording(self) -> bool:
        return self.recording_sensor.start_recording()

    def stop_recording(self) -> bool:
        return self.recording_sensor.stop_recording()


