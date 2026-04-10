from enum import Enum

from mission_control.ai.prompt_helpers.drone_prompt_generation import fs1_prompt, fs2_prompt


class Prompts(str, Enum):
    FS1 = "FS-1"
    FS2 = "FS-2"


PROMPT_FACTORIES = {
    Prompts.FS1: fs1_prompt,
    Prompts.FS2: fs2_prompt,
}
