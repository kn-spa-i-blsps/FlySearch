import asyncio
import io
import logging
from typing import Any

import httpx
from google import genai
from google.genai import types
from google.genai.errors import APIError

from mission_control.ai.conversation.tool_conversation import (
    SKIPPED_CALL_RESULT,
    AssistantReply,
    ToolCall,
    ToolConversation,
)
from mission_control.ai.prompt_helpers.training_prompt import TOOLS

RETRYABLE_CODES = {429, 499, 500, 503, 504}

FUNCTION_DECLARATIONS = [
    types.FunctionDeclaration(
        name=t["function"]["name"],
        description=t["function"]["description"],
        parameters=t["function"]["parameters"],
    )
    for t in TOOLS
]


def _image_part(image) -> types.Part:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg")


def _to_parts(parts: list[Any]) -> list[types.Part]:
    return [types.Part.from_text(text=p) if isinstance(p, str) else _image_part(p) for p in parts]


class GeminiConversation(ToolConversation):
    """
    Gemini function calling, mirroring src/model/gsplat_tools_policy.py from the
    training repository: same system prompt and tool declarations, the move
    observation goes back as a function response plus the new camera image.
    """

    def __init__(
        self,
        client: genai.Client,
        model_name: str,
        max_tokens=None,
        retries: int = 4,
        retry_delay: float = 5.0,
    ):
        super().__init__()
        self.client = client
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.retries = retries
        self.retry_delay = retry_delay
        self.logger = logging.getLogger(__name__)

    def _append_user(self, parts):
        self.native.append(types.Content(role="user", parts=_to_parts(parts)))

    def _append_tool_results(self, calls, parts):
        first, *rest = calls
        text = "\n".join(p for p in parts if isinstance(p, str))
        images = [p for p in parts if not isinstance(p, str)]
        response_parts = [types.Part.from_function_response(name=first.name, response={"result": text})]
        response_parts += [
            types.Part.from_function_response(name=c.name, response={"result": SKIPPED_CALL_RESULT}) for c in rest
        ]
        self.native.append(types.Content(role="user", parts=response_parts + _to_parts(images)))

    def _append_assistant_text(self, text):
        self.native.append(types.Content(role="model", parts=[types.Part.from_text(text=text)]))

    def _config(self) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            tools=[types.Tool(function_declarations=FUNCTION_DECLARATIONS)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(include_thoughts=True),
            max_output_tokens=self.max_tokens,
        )

    async def _generate_with_retry(self) -> types.GenerateContentResponse:
        delay = self.retry_delay
        for attempt in range(1, self.retries + 1):
            try:
                return await self.client.aio.models.generate_content(
                    model=self.model_name, contents=self.native, config=self._config()
                )
            except (APIError, httpx.TimeoutException) as e:
                code = getattr(e, "code", None)
                if isinstance(e, APIError) and code not in RETRYABLE_CODES:
                    raise
                if attempt == self.retries:
                    raise RuntimeError(f"Gemini failed after {self.retries} attempts: {e}") from e
                self.logger.warning(
                    f"Gemini error ({code or type(e).__name__}), attempt {attempt}/{self.retries}. "
                    f"Retrying in {delay:.0f}s..."
                )
                await asyncio.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    async def _generate(self) -> AssistantReply:
        response = await self._generate_with_retry()

        candidate = response.candidates[0] if response.candidates else None
        content = candidate.content if candidate else None
        if content is None or not content.parts:
            reason = candidate.finish_reason if candidate else response.prompt_feedback
            raise RuntimeError(f"Gemini returned no content (reason: {reason})")

        # Sent back unchanged so Gemini keeps its thought signatures.
        self.native.append(content)

        text, thoughts, calls = [], [], []
        for part in content.parts:
            if part.function_call is not None:
                calls.append(
                    ToolCall(
                        name=part.function_call.name,
                        arguments=dict(part.function_call.args or {}),
                        call_id=part.function_call.id,
                    )
                )
            elif part.text:
                (thoughts if part.thought else text).append(part.text)

        self.logger.info(f"LLM response: {''.join(text)!r}, calls: {calls}")
        return AssistantReply(text="".join(text), reasoning="".join(thoughts), tool_calls=calls)
