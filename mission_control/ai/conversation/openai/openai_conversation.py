import asyncio
import base64
import io
import json
from typing import Any

from openai import Client
from PIL import Image

from mission_control.ai.conversation.tool_conversation import (
    SKIPPED_CALL_RESULT,
    AssistantReply,
    ToolCall,
    ToolConversation,
)
from mission_control.ai.prompt_helpers.training_prompt import TOOLS
from mission_control.utils.logger import get_configured_logger

logger = get_configured_logger(__name__)


def image_to_data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("utf-8")


def _parse_arguments(arguments: str | None) -> dict[str, Any]:
    if not arguments:
        return {}
    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        logger.warning(f"Tool call arguments are not valid JSON: {arguments}")
        return {}


class OpenAIChatConversation(ToolConversation):
    """
    Chat Completions API with tools - used for self-hosted vLLM models (FlyLoRA, base Qwen).

    The tool schemas are also passed through ``chat_template_kwargs`` because vLLM
    strips the ``return`` entries from ``tools`` before rendering, while TRL rendered
    them into the training prompt. ``tools`` itself is still needed for vLLM's tool
    call parser.
    """

    def __init__(
        self,
        client: Client,
        model_name: str,
        max_tokens: int | None = None,
        chat_template_kwargs: dict[str, Any] | None = None,
    ):
        super().__init__()
        self.client = client
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.chat_template_kwargs = {"tools": TOOLS, **(chat_template_kwargs or {})}

    @staticmethod
    def _content(parts: list[Any]) -> list[dict[str, Any]]:
        return [
            {"type": "text", "text": part}
            if isinstance(part, str)
            else {"type": "image_url", "image_url": {"url": image_to_data_url(part)}}
            for part in parts
        ]

    def _append_user(self, parts):
        self.native.append({"role": "user", "content": self._content(parts)})

    def _append_tool_results(self, calls, parts):
        first, *rest = calls
        self.native.append(
            {"role": "tool", "tool_call_id": first.call_id, "name": first.name, "content": self._content(parts)}
        )
        for call in rest:
            self.native.append(
                {"role": "tool", "tool_call_id": call.call_id, "name": call.name, "content": SKIPPED_CALL_RESULT}
            )

    def _append_assistant_text(self, text):
        self.native.append({"role": "assistant", "content": text})

    def _messages(self) -> list[dict[str, Any]]:
        system = [{"role": "system", "content": self.system_prompt}] if self.system_prompt else []
        return system + self.native

    async def _generate(self) -> AssistantReply:
        kwargs = {}
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens

        response = await asyncio.to_thread(
            self.client.chat.completions.create,
            model=self.model_name,
            messages=self._messages(),
            tools=[{"type": t["type"], "function": {k: v for k, v in t["function"].items() if k != "return"}} for t in TOOLS],
            extra_body={"chat_template_kwargs": self.chat_template_kwargs},
            **kwargs,
        )
        choice = response.choices[0]
        message = choice.message
        # vLLM returns the thinking block separately when started with --reasoning-parser.
        reasoning = getattr(message, "reasoning_content", None) or getattr(message, "reasoning", None) or ""
        logger.info(f"llm response (finish_reason={choice.finish_reason}): {message.content!r}")

        calls = [
            ToolCall(name=tc.function.name, arguments=_parse_arguments(tc.function.arguments), call_id=tc.id)
            for tc in (message.tool_calls or [])
        ]

        assistant: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if reasoning:
            # The Qwen chat template re-renders it inside <think>, as it was during training.
            # vLLM (0.23) reads it back from "reasoning" and silently drops "reasoning_content".
            assistant["reasoning"] = reasoning
        if message.tool_calls:
            assistant["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]
        self.native.append(assistant)

        return AssistantReply(text=message.content or "", reasoning=reasoning, tool_calls=calls)
