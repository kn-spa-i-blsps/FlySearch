from enum import Enum

from mission_control.ai.prompt_helpers.training_prompt import build_system_prompt


class Prompts(str, Enum):
    # System prompt FlyLoRA was trained with; the model acts through move/found tool calls.
    RL = "RL"


PROMPT_FACTORIES = {
    Prompts.RL: build_system_prompt,
}
