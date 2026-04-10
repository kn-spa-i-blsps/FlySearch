from enum import Enum

from mission_control.ai.conversation.gemini.gemini_factory import GeminiFactory
from mission_control.ai.conversation.openai.openai_factory import OpenAIFactory


class LLMBackends(str, Enum):
    GPT = "openai"
    GEMINI = "gemini"


LLM_BACKEND_FACTORIES = {
    LLMBackends.GPT: OpenAIFactory,
    LLMBackends.GEMINI: GeminiFactory,
}
