import os

from openai import OpenAI

from mission_control.ai.conversation.base_conversation_factory import (
    BaseConversationFactory,
)
from mission_control.ai.conversation.openai.openai_conversation import (
    OpenAIChatConversation,
)
from mission_control.ai.conversation.openai.openai_responses_conversation import (
    OpenAIResponsesConversation,
)

REQUEST_TIMEOUT_S = 90.0
# The SDK retries connection errors, 408/409/429 and 5xx with exponential backoff.
MAX_RETRIES = 2


def _optional_int_env(name: str) -> int | None:
    raw = os.environ.get(name)
    return int(raw) if raw else None


class OpenAIFactory(BaseConversationFactory):
    """OpenAI cloud models through the Responses API."""

    def __init__(self, model_name: str):
        # base_url is explicit - the SDK would otherwise pick up OPENAI_BASE_URL from the environment.
        self.client = OpenAI(
            api_key=os.environ["OPEN_AI_KEY"],
            base_url="https://api.openai.com/v1",
            timeout=REQUEST_TIMEOUT_S,
            max_retries=MAX_RETRIES,
        )
        self.model_name = model_name.removeprefix("oai-")

    def get_conversation(self):
        return OpenAIResponsesConversation(
            self.client,
            model_name=self.model_name,
            max_output_tokens=_optional_int_env("OPENAI_MAX_OUTPUT_TOKENS"),
        )


class VllmFactory(BaseConversationFactory):
    """Self-hosted models (FlyLoRA / base Qwen) behind vLLM's OpenAI-compatible server."""

    def __init__(self, model_name: str):
        self.client = OpenAI(
            api_key=os.environ.get("VLLM_API_KEY") or "not-needed",
            base_url=os.environ["VLLM_BASE_URL"],
            timeout=REQUEST_TIMEOUT_S,
            max_retries=MAX_RETRIES,
        )
        self.model_name = model_name

    def get_conversation(self):
        # Thinking "on" matches how FlyLoRA was trained (--enable-thinking defaults to True).
        # VLLM_MAX_TOKENS bounds worst-case latency of the thinking block during a live mission.
        disable_thinking = os.environ.get("VLLM_DISABLE_THINKING", "").strip().lower() in ("1", "true", "yes")
        return OpenAIChatConversation(
            self.client,
            model_name=self.model_name,
            max_tokens=_optional_int_env("VLLM_MAX_TOKENS"),
            chat_template_kwargs={"enable_thinking": not disable_thinking},
        )
