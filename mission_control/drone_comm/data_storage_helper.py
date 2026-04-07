import json
import os
from datetime import datetime
from pathlib import Path

from mission_control.core.config import Config
from mission_control.utils.logger import get_configured_logger

logger = get_configured_logger(__name__)

class MemoryDataStorageHelper:
    def __init__(self, config: Config):
        self.config = config

    async def _handle_binary_photo(self, ws, message):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_base = f"img_{ts}"
        file_name = f"{file_base}.jpg"
        path = os.path.join(self.config.upload_dir, file_name)
        with open(path, "wb") as f:
            f.write(message)
        logger.debug(f"[WS] saved binary -> {path}")
        await ws.send(f"[SERVER] SAVED {path}")

    async def _handle_telemetry(self, data, photo_name=None) -> Path:
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


    async def _handle_telemetry_photo(self, ws, photo_base64: bytes, telemetry):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Photo
        if not photo_base64:
            logger.warning("[WS] Received 'PHOTO_WITH_TELEMETRY' but 'photo' field is missing; skipping frame.")
            await self._handle_telemetry(telemetry, None)
            return

        try:
            photo_data = base64.b64decode(photo_base64)
        except (TypeError, ValueError) as e:
            raise DroneInvalidDataError(f"Failed to decode Base64 photo data: {e}") from e

        img_file_base = f"img_{ts}"
        img_file_name = f"{img_file_base}.jpg"
        img_path = os.path.join(self.config.upload_dir, img_file_name)

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

        # We are caching paths for easier access after, when sending to VLM.
        self.mission_context.last_photo_path_cache = img_path

        # Telemetry
        await self._handle_telemetry(telemetry, img_file_name)

        if self.mission_context.photo_received_event:
            self.mission_context.photo_received_event.set()
