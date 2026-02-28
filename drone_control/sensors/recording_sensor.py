from datetime import datetime
from pathlib import Path

from drone_control.core.exceptions import SensorError
from drone_control.sensors.base import Sensor
from drone_control.sensors.camera_capture_backend import (
    recording_status,
    start_video_recording,
    stop_video_recording,
)

class RecordingSensor(Sensor):
    """Camera recording sensor backed by shared Picamera2 runtime."""

    name = "recording"

    def __init__(
        self,
        *,
        video_dir: str | Path = "/video",
        width: int = 640,
        height: int = 480,
        quality: int = 90,
        video_device: str = "/dev/video0",
        bitrate: int = 10_000_000,
    ):
        self.video_dir = Path(video_dir)
        self.width = width
        self.height = height
        self.quality = quality
        self.video_device = video_device
        self.bitrate = bitrate
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self._recording = False
        self._current_path: Path | None = None

    def health(self) -> dict[str, object]:
        status = self.status()
        return {
            "sensor": self.name,
            "implemented": True,
            "video_device": self.video_device,
            "video_dir": str(self.video_dir),
            "recording": status.get("recording", False),
            "path": status.get("path"),
        }

    def start_recording(self) -> bool:
        status = recording_status()
        if bool(status.get("recording")):
            self._recording = True
            path = status.get("path")
            self._current_path = Path(path) if isinstance(path, str) else None
            print(f"[RPi] Recording already active: {path}")
            return True

        destination = self.video_dir / f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.h264"

        try:
            started = start_video_recording(
                destination=destination,
                width=self.width,
                height=self.height,
                bitrate=self.bitrate,
            )
        except Exception as exc:
            raise SensorError(f"Recording capture failed: {exc}") from exc

        self._recording = bool(started.get("recording", False))
        path = started.get("path")
        self._current_path = Path(path) if isinstance(path, str) else destination
        return self._recording

    def stop_recording(self) -> bool:
        try:
            stopped = stop_video_recording()
        except Exception as exc:
            raise SensorError(f"Recording stop failed: {exc}") from exc

        self._recording = bool(stopped.get("recording", False))
        path = stopped.get("path")
        self._current_path = Path(path) if isinstance(path, str) else self._current_path
        return not self._recording

    def status(self) -> dict[str, object]:
        status = recording_status()
        self._recording = bool(status.get("recording", False))
        path = status.get("path")
        if isinstance(path, str):
            self._current_path = Path(path)
        return {
            "recording": self._recording,
            "path": str(self._current_path) if self._current_path else path,
        }
