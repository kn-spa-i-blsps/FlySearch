from dataclasses import dataclass


@dataclass
class MissionContext:
    # Global variable for VLM communication
    conversation = None

    # Cache of last saved photo, telemetry and prompt (for easy access)
    last_photo_path_cache = None
    last_telemetry_path_cache = None
    last_prompt_text_cache = None