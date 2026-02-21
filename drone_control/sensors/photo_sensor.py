import pathlib
import tempfile

from drone_control.core.exceptions import SensorError
from drone_control.sensors.camera_capture_backend import capture_photo
from drone_control.sensors.base import Sensor


class PhotoSensor(Sensor):
    """Capture one camera frame and return it as raw JPEG bytes."""
    name = "photo"

    def __init__(
        self,
        *,
        width: int,
        height: int,
        quality: int,
        video_device: str,
    ):
        self.width = width
        self.height = height
        self.quality = quality
        self.video_device = video_device

    def health(self) -> dict[str, object]:
        return {
            "sensor": self.name,
            "video_device": self.video_device,
        }

    def capture_bytes(self) -> bytes:
        with tempfile.NamedTemporaryFile(prefix="flysearch_photo_", suffix=".jpg", delete=False) as tmp:
            tmp_path = pathlib.Path(tmp.name)

        try:
            capture_photo(
                destination=tmp_path,
                width=self.width,
                height=self.height,
                quality=self.quality,
                video_device=self.video_device,
            )
            with tmp_path.open("rb") as file_obj:
                return file_obj.read()
        except Exception as exc:
            raise SensorError(f"Photo capture failed: {exc}") from exc
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
