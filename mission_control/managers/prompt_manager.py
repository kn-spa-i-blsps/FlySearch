import json
import os
from datetime import datetime
from typing import Dict

from mission_control.core.config import Config
from mission_control.core.mission_context import MissionContext
from prompt_generation.prompts import Prompts, PROMPT_FACTORIES


class PromptManager:
    """ Class for prompt management - generating, saving..."""

    def __init__(self, config : Config, mission_context : MissionContext):
        self.config = config
        self.mission_context = mission_context

    def _generate_prompt(self, kind: str, kv: Dict[str, str]) -> Dict[str, str]:
        """ Generate prompt using prompt factories.

            :param kind: kind of prompt - FS-1 | FS-2
            :param kv: parameters for prompt generation (glimpses, object, [area])
        """

        params = {
            "object": kv.get("object", "helipad"),
            "glimpses": int(kv.get("glimpses", "6")),
            "area": int(kv.get("area", "80")),          # area is only for FS-1
        }
        t = Prompts(kind)
        factory = PROMPT_FACTORIES[t]
        if t == Prompts.FS1:
            text = factory(params["glimpses"], params["object"], params["area"])
        else:
            text = factory(params["glimpses"], params["object"])
        return {"kind": kind, "text": text, **params}

    def _save_prompt(self, prompt_meta: Dict[str, str]) -> Dict[str, str]:
        """ Save prompt as JSON and txt and return JSON with paths to both. """

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{prompt_meta['kind'].lower()}_{ts}"
        txt_path = os.path.join(self.config.prompts_dir, base + ".txt")

        self.mission_context.last_prompt_text_cache = prompt_meta["text"]

        json_path = os.path.join(self.config.prompts_dir, base + ".json")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(prompt_meta["text"])
        meta_to_save = dict(prompt_meta)
        meta_to_save.pop("text", None)
        meta_to_save["saved_at"] = ts
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(meta_to_save, f, ensure_ascii=False, indent=2)
        return {"txt": txt_path, "json": json_path}

    def generate_and_save(self, kind, kv):
        """ Generates and saves a system prompt based on the specified 'kind' and parameters 'kv'. """

        try:
            meta = self._generate_prompt(kind, kv)
            saved = self._save_prompt(meta)
        except Exception as e:
            print(f"Error in _generate_prompt or _save_prompt: {e}")
            return

        print(f"[PROMPT] saved -> {saved['txt']} (+meta {saved['json']})")