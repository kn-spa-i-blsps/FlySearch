import os

from openai import OpenAI, _types

from conversation.base_conversation_factory import BaseConversationFactory
from conversation.openai.openai_conversation import OpenAIConversation


class OpenAIFactory(BaseConversationFactory):
    def __init__(self, model_name: str):
        # OPENAI_BASE_URL lets this backend target any OpenAI-compatible
        # endpoint (e.g. a self-hosted vLLM server serving flylora / base
        # Qwen), not just api.openai.com. Unset -> unchanged default behavior.
        base_url = os.environ.get("OPENAI_BASE_URL") or None
        self.client = OpenAI(api_key=os.environ["OPEN_AI_KEY"], base_url=base_url)
        self.model_name = model_name.removeprefix("oai-")

    def get_conversation(self):
        # Self-hosted "thinking" models (flylora / base Qwen3.5 via OPENAI_BASE_URL) can
        # reason indefinitely with no cap, which is fine for offline eval but unusable for
        # a live drone mission. OPENAI_MAX_TOKENS lets a self-hosted deployment bound worst-case
        # latency; left unset, real OpenAI cloud models keep the existing NotGiven behavior
        # (needed for GPT-5 compatibility).
        max_tokens_env = os.environ.get("OPENAI_MAX_TOKENS")
        max_tokens = int(max_tokens_env) if max_tokens_env else _types.NotGiven()

        # Qwen3-family "thinking" models take a per-request chat_template_kwargs flag
        # (vLLM-specific, ignored/rejected by real OpenAI cloud models - hence gated behind
        # an env var rather than always sending it) that controls whether the chat template
        # opens an empty <think></think> (answers directly) or a real one (extended reasoning
        # before answering). Both are legitimate configurations to benchmark, not just a fix.
        disable_thinking = os.environ.get("OPENAI_DISABLE_THINKING", "").strip().lower() in ("1", "true", "yes")
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}} if disable_thinking else None

        return OpenAIConversation(
            self.client,
            model_name=self.model_name,
            max_tokens=max_tokens,
            # We have to do this because otherwise GPT-5 would stop working. 4o works with default arguments for this class, but while making this compatible with GPT-5 I've decided to stop passing these arguments altogether as they don't break the 4o.
            temperature=_types.NotGiven(),
            extra_body=extra_body
        )
