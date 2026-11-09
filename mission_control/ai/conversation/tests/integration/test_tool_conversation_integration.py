import asyncio
import os

import pytest
from PIL import Image

from mission_control.ai.conversation.abstract_conversation import Role
from mission_control.ai.conversation.conversations import LLM_BACKEND_FACTORIES
from mission_control.ai.prompt_helpers.training_prompt import (
    INITIAL_USER_TEXT,
    build_system_prompt,
    format_bounds,
    format_initial_observation,
    format_position,
    format_step_observation,
)
from mission_control.ai.vision.vlm_bridge import reply_to_model_response

# Mark all tests in this file as integration tests
pytestmark = pytest.mark.integration

BACKENDS = [
    ("openai", os.getenv("TEST_OPENAI_MODEL", "gpt-6-luna"), "OPEN_AI_KEY"),
    ("gemini", os.getenv("TEST_GEMINI_MODEL", "gemini-3.5-flash"), "GEMINI_AI_KEY"),
    ("vllm", os.getenv("TEST_VLLM_MODEL", "flylora"), "VLLM_BASE_URL"),
]


@pytest.mark.parametrize("backend, model_name, env_var", BACKENDS)
def test_two_step_mission_with_tool_calls(backend, model_name, env_var):
    """Initial observation, then a move result with a new image, against the real API."""
    if not os.getenv(env_var):
        pytest.skip(f"{env_var} not set.")

    async def run():
        conversation = LLM_BACKEND_FACTORIES[backend](model_name).get_conversation()
        conversation.begin_transaction(Role.SYSTEM)
        conversation.add_text_message(build_system_prompt())
        await conversation.commit_transaction()

        image = Image.new("RGB", (500, 500), (90, 120, 80))
        bounds = format_bounds(40, 10, 120)
        steps = [
            [INITIAL_USER_TEXT, image, format_initial_observation("Find helipad", format_position(0, 0, 30), bounds, 10)],
            [image, format_step_observation("Movement successful.", format_position(0, 10, 30), 1, 10, bounds)],
        ]
        for parts in steps:
            conversation.begin_transaction(Role.USER)
            for part in parts:
                if isinstance(part, str):
                    conversation.add_text_message(part)
                else:
                    await conversation.add_image_message(part)
            await conversation.commit_transaction(send_to_vlm=True)

            response = reply_to_model_response(conversation.last_reply)
            assert response.found or len(response.move) == 3

    asyncio.run(run())
