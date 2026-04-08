import base64
import json
from datetime import datetime
from pathlib import Path

from mission_control.core.config import Config
from mission_control.core.exceptions import DroneInvalidDataError
from mission_control.core.interfaces import DataStorageHelper
from mission_control.utils.image_processing import crop_img_square
from mission_control.utils.logger import get_configured_logger

logger = get_configured_logger(__name__)


class FileDataStorageHelper(DataStorageHelper):
    def __init__(self, config: Config):
        self.config = config

    async def _save_telemetry(self, data, photo_name=None) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_base = f"telemetry_{ts}"
        file_name = f"{file_base}.json"
        path = self.config.telemetry_dir / file_name

        payload = {
            "received_at": ts,
            "associated_photo": photo_name,
            "data": data
        }

        with open(path, "w", encoding="utf-8") as f:
            try:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                logger.debug(f"[WS] saved telemetry -> {path}")
            except Exception as e:
                logger.warning(f"[WS] error saving telemetry: {e}")

        return path

    async def save_photo_and_telemetry(self, photo_base64: bytes, telemetry):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Photo
        if not photo_base64:
            logger.warning("[WS] Received 'PHOTO_WITH_TELEMETRY' but 'photo' field is missing; skipping frame.")
            telemetry_path = await self._save_telemetry(telemetry, None)
            return None, telemetry_path

        try:
            photo_data = base64.b64decode(photo_base64)
        except (TypeError, ValueError) as e:
            raise DroneInvalidDataError(f"Failed to decode Base64 photo data: {e}") from e

        img_file_base = f"img_{ts}"
        img_file_name = f"{img_file_base}.jpg"
        img_path = self.config.upload_dir / img_file_name

        # Crop the image to be square (as in original paper).
        try:
            img_cropped, side = crop_img_square(photo_data)

            img_cropped.save(img_path, format="JPEG", quality=90)
            logger.debug(f"[WS] saved *square* photo -> {img_path} ({side}x{side})")
        except Exception as e:
            logger.warning(f"[WS] square crop failed, saving raw photo: {e}")
            with open(img_path, "wb") as f:
                f.write(photo_data)
                logger.debug(f"[WS] saved photo (raw) -> {img_path}")

        # Telemetry
        telemetry_path = await self._save_telemetry(telemetry, img_file_name)

        return img_path, telemetry_path
