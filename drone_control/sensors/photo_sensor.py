import pathlib

from drone_control.core.exceptions import SensorError
from drone_control.sensors.camera_capture_backend import capture_photo
from drone_control.sensors.base import Sensor


class PhotoSensor(Sensor):
    name = "photo"

    def __init__(
        self,
        *,
        img_dir: pathlib.Path,
        file_name: str,
        width: int,
        height: int,
        quality: int,
        video_device: str,
    ):
        self.img_dir = img_dir
        self.file_name = file_name
        self.width = width
        self.height = height
        self.quality = quality
        self.video_device = video_device

    @property
    def photo_path(self) -> pathlib.Path:
        return self.img_dir / self.file_name

    def health(self) -> dict[str, object]:
        return {
            "sensor": self.name,
            "img_dir": str(self.img_dir),
            "video_device": self.video_device,
        }

    def capture(self) -> pathlib.Path:
        try:
            capture_photo(
                destination=self.photo_path,
                width=self.width,
                height=self.height,
                quality=self.quality,
                video_device=self.video_device,
            )
        except Exception as exc:
            raise SensorError(f"Photo capture failed: {exc}") from exc

        return self.photo_path

    def read_bytes(self) -> bytes:
        path = self.photo_path
        try:
            with path.open("rb") as file_obj:
                return file_obj.read()
        except Exception as exc:
            raise SensorError(f"Failed to read photo at {path}: {exc}") from exc
