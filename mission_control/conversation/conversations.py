from enum import Enum

from mission_control.conversation.openai.openai_factory import OpenAIFactory
from mission_control.conversation.gemini.gemini_factory import GeminiFactory


class LLMBackends(str, Enum):
    GPT = "openai"
    GEMINI = "gemini"


LLM_BACKEND_FACTORIES = {
    LLMBackends.GPT: OpenAIFactory,
    LLMBackends.GEMINI: GeminiFactory,
}
