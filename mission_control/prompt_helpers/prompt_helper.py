import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict

import aiofiles

from mission_control.core.config import Config
from mission_control.core.interfaces import PromptHelper
from mission_control.prompt_helpers.prompts import Prompts, PROMPT_FACTORIES

logger = logging.getLogger(__name__)


class FlySearchPromptHelper(PromptHelper):
    """
    Concrete implementation of PromptHelper that generates prompts
    and asynchronously logs them to the local file system.
    """

    def __init__(self, config: Config):
        self.config = config

    async def generate_prompt(self, kind: str, args: Dict[str, str]) -> str:
        """ Implements the main method of the PromptHelper interface. """

        # 1. Prompt Generation
        try:
            params = {
                "object": args.get("object", "helipad"),
                "glimpses": int(args.get("glimpses", "6")),
                "area": int(args.get("area", "80")),
                "minimum_altitude": int(args.get("minimum_altitude", "10")),
            }

            t = Prompts(kind)
            factory = PROMPT_FACTORIES[t]

            if t == Prompts.FS1:
                text = factory(params["glimpses"], params["object"], params["area"], params["minimum_altitude"])
            else:
                text = factory(params["glimpses"], params["object"], params["minimum_altitude"])

            prompt_meta = {"kind": kind, "text": text, **params}

        except Exception as e:
            logger.error(f"[PROMPT] Failed to generate prompt: {e}")
            raise ValueError(f"Failed to generate prompt of kind {kind}") from e

        # 2. Background save (hidden implementation detail, non-blocking)
        asyncio.create_task(self._save_prompt_to_disk(prompt_meta))

        return text

    async def _save_prompt_to_disk(self, prompt_meta: Dict[str, str]) -> None:
        """
        Internal, private method for saving the prompt to disk.
        Invisible to the Orchestrator.
        """
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = f"{prompt_meta['kind'].lower()}_{ts}"
            prompts_dir = Path(self.config.prompts_dir)
            txt_path = prompts_dir / f"{base}.txt"
            json_path = prompts_dir / f"{base}.json"

            text_to_save = prompt_meta["text"]

            # Create a copy of meta without the raw text for JSON storage
            meta_to_save = dict(prompt_meta)
            meta_to_save.pop("text", None)
            meta_to_save["saved_at"] = ts

            # Async file writing to prevent Event Loop blocking
            async with aiofiles.open(txt_path, "w", encoding="utf-8") as f:
                await f.write(text_to_save)

            async with aiofiles.open(json_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(meta_to_save, ensure_ascii=False, indent=2))

            logger.debug(f"[PROMPT] Successfully saved -> {txt_path} (+meta {json_path})")

        except Exception as e:
            logger.warning(f"[PROMPT] Failed to save prompt to disk: {e}")