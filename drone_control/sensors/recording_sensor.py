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
        record_fps: int = 30,
        quality: int = 90,
        video_device: str = "/dev/video0"
    ):
        self.video_dir = Path(video_dir)
        self.width = width
        self.height = height
        self.record_fps = max(1, int(record_fps))
        self.quality = quality
        self.video_device = video_device
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
            "record_fps": self.record_fps,
            "recording": status.get("recording", False),
            "path": status.get("path"),
            "metadata_path": status.get("metadata_path"),
        }

    def start_recording(self) -> dict[str, object]:
        destination = self.video_dir / f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.h264"

        try:
            started = start_video_recording(
                destination=destination,
                width=self.width,
                height=self.height,
                record_fps=self.record_fps,
            )
        except Exception as exc:
            raise SensorError(f"Recording capture failed: {exc}") from exc

        self._recording = bool(started.get("recording", False))
        path = started.get("path")
        self._current_path = Path(path) if isinstance(path, str) else destination
        ref_count_raw = started.get("ref_count", 0)
        ref_count = ref_count_raw if isinstance(ref_count_raw, int) else 0
        return {
            "ok": bool(started.get("ok", True)),
            "recording": self._recording,
            "path": str(self._current_path) if self._current_path else None,
            "ref_count": ref_count,
            "metadata_path": started.get("metadata_path"),
            "metadata": started.get("metadata"),
        }

    def stop_recording(self) -> dict[str, object]:
        try:
            stopped = stop_video_recording()
        except Exception as exc:
            raise SensorError(f"Recording stop failed: {exc}") from exc

        self._recording = bool(stopped.get("recording", False))
        path = stopped.get("path")
        self._current_path = Path(path) if isinstance(path, str) else self._current_path
        ref_count_raw = stopped.get("ref_count", 0)
        ref_count = ref_count_raw if isinstance(ref_count_raw, int) else 0
        return {
            "ok": bool(stopped.get("ok", True)),
            "recording": self._recording,
            "path": str(self._current_path) if self._current_path else None,
            "ref_count": ref_count,
            "metadata_path": stopped.get("metadata_path"),
            "metadata": stopped.get("metadata"),
        }

    def status(self) -> dict[str, object]:
        status = recording_status()
        self._recording = bool(status.get("recording", False))
        path = status.get("path")
        if isinstance(path, str):
            self._current_path = Path(path)
        ref_count_raw = status.get("ref_count", 0)
        ref_count = ref_count_raw if isinstance(ref_count_raw, int) else 0
        return {
            "recording": self._recording,
            "path": str(self._current_path) if self._current_path else path,
            "ref_count": ref_count,
            "metadata_path": status.get("metadata_path"),
            "metadata": status.get("metadata"),
        }
