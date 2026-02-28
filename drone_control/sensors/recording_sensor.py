from pathlib import Path

from drone_control.core.exceptions import SensorError
from drone_control.sensors.base import Sensor
from drone_control.sensors.camera_capture_backend import capture_video

class RecordingSensor(Sensor):
    """Extension-ready camera recording sensor (currently noop)."""

    name = "recording"

    def __init__(
        self,
        *,
        destination: Path,
        width: int,
        height: int,
        quality: int,
        video_device: str
    ):
        self.destination = destination
        self.width = width
        self.height = height
        self.quality = quality
        self.video_device = video_device
        self._recording = False

    def health(self) -> dict[str, object]:
        return {
            "sensor": self.name,
            "video_device": self.video_device,
            "recording": self._recording
        }

    def start_recording(self) -> bool:
        try:
            capture_video(
                destination=self.destination,
                width=self.width,
                height=self.height,
                quality=self.quality,
                video_device=self.video_device
            )
            self._recording = True
        except Exception as exc:
            raise SensorError(f"Recording capture failed: {exc}") from exc
        return False

    def stop_recording(self) -> bool:
        self._recording = False
        return False

    def status(self) -> dict[str, object]:
        return {"recording": self._recording}
