import json
from datetime import datetime
from pathlib import Path
from typing import Any

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

    def list_recordings(self) -> list[dict[str, object]]:
        def safe_mtime(recording_path: Path) -> float:
            try:
                return recording_path.stat().st_mtime
            except OSError:
                return 0.0

        rows: list[dict[str, object]] = []
        for path in sorted(self.video_dir.glob("*.h264"), key=safe_mtime, reverse=True):
            try:
                stat = path.stat()
            except OSError:
                continue

            metadata_path = path.with_suffix(".json")
            row: dict[str, object] = {
                "name": path.name,
                "size_bytes": int(stat.st_size),
                "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y%m%d_%H%M%S"),
                "metadata_exists": metadata_path.exists(),
            }

            if bool(row["metadata_exists"]):
                try:
                    with metadata_path.open("r", encoding="utf-8") as file_obj:
                        metadata = json.load(file_obj)
                    if isinstance(metadata, dict):
                        record_fps = metadata.get("record_fps")
                        if isinstance(record_fps, int):
                            row["record_fps"] = record_fps
                        elif isinstance(record_fps, str) and record_fps.isdigit():
                            row["record_fps"] = int(record_fps)
                except Exception as exc:
                    row["metadata_error"] = str(exc)

            rows.append(row)
        return rows

    def prepare_recordings_for_pull(
        self,
        names: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        prepared: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []

        seen: set[str] = set()
        for raw_name in names:
            if not isinstance(raw_name, str):
                rejected.append({"name": str(raw_name), "error": "invalid_name_type"})
                continue

            name = raw_name.strip()
            if not name:
                rejected.append({"name": raw_name, "error": "empty_name"})
                continue

            if name in seen:
                continue
            seen.add(name)

            candidate = Path(name)
            if candidate.name != name:
                rejected.append({"name": name, "error": "invalid_name"})
                continue

            if candidate.suffix.lower() != ".h264":
                rejected.append({"name": name, "error": "not_h264"})
                continue

            video_path = (self.video_dir / candidate.name)
            if not video_path.exists():
                rejected.append({"name": name, "error": "not_found"})
                continue

            try:
                stat = video_path.stat()
            except OSError:
                rejected.append({"name": name, "error": "stat_failed"})
                continue

            metadata_path = video_path.with_suffix(".json")
            metadata_exists = metadata_path.exists()
            metadata: dict[str, Any] | None = None
            metadata_error: str | None = None

            if metadata_exists:
                try:
                    with metadata_path.open("r", encoding="utf-8") as file_obj:
                        loaded = json.load(file_obj)
                    if isinstance(loaded, dict):
                        metadata = loaded
                except Exception as exc:
                    metadata_error = str(exc)

            entry: dict[str, Any] = {
                "name": candidate.name,
                "path": str(video_path),
                "size_bytes": int(stat.st_size),
                "metadata_exists": metadata_exists,
                "metadata": metadata,
            }
            if metadata_error:
                entry["metadata_error"] = metadata_error
            prepared.append(entry)

        return prepared, rejected
