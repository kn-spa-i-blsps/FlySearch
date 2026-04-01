import asyncio
from dataclasses import dataclass


@dataclass
class MissionContext:
    """ Holds information about current mission state. """

    stop = asyncio.Event()              # Interrupt flag.
    # Object for the conversation with the VLM.
    conversation = None                 # ChatManager
    parsed_response = None              # VLMBridge

    # Cache of last saved photo, telemetry and prompt (for easy access).
    last_photo_path_cache = None        # DroneBridge
    last_telemetry_path_cache = None    # DroneBridge
    last_prompt_text_cache = None       # PromptManager

    photo_received_event: asyncio.Event = None

    current_decision_future = None # Future for decision-making.

    last_chat_name = None
    search_interrupted = False
    moves_performed = 0