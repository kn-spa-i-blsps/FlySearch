from dataclasses import dataclass


@dataclass
class MissionContext:
    """ Holds information about current mission state. """

    # Object for the conversation with the VLM.
    conversation = None

    # Cache of last saved photo, telemetry and prompt (for easy access).
    last_photo_path_cache = None
    last_telemetry_path_cache = None
    last_prompt_text_cache = None