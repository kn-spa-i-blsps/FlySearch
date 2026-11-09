import asyncio
from typing import Any

from openai import Client

from mission_control.ai.conversation.openai.openai_conversation import (
    _parse_arguments,
    image_to_data_url,
)
from mission_control.ai.conversation.tool_conversation import (
    SKIPPED_CALL_RESULT,
    AssistantReply,
    ToolCall,
    ToolConversation,
)
from mission_control.ai.prompt_helpers.training_prompt import TOOLS
from mission_control.utils.logger import get_configured_logger

logger = get_configured_logger(__name__)

RESPONSES_TOOLS = [
    {
        "type": "function",
        "name": t["function"]["name"],
        "description": t["function"]["description"],
        "parameters": t["function"]["parameters"],
    }
    for t in TOOLS
]


class OpenAIResponsesConversation(ToolConversation):
    """
    Responses API with function tools - used for OpenAI cloud models. Newer reasoning
    models (e.g. gpt-6-luna) reject function tools on Chat Completions.
    """

    def __init__(self, client: Client, model_name: str, max_output_tokens: int | None = None):
        super().__init__()
        self.client = client
        self.model_name = model_name
        self.max_output_tokens = max_output_tokens

    @staticmethod
    def _content(parts: list[Any]) -> list[dict[str, Any]]:
        return [
            {"type": "input_text", "text": part}
            if isinstance(part, str)
            else {"type": "input_image", "image_url": image_to_data_url(part)}
            for part in parts
        ]

    def _append_user(self, parts):
        self.native.append({"role": "user", "content": self._content(parts)})

    def _append_tool_results(self, calls, parts):
        first, *rest = calls
        self.native.append(
            {"type": "function_call_output", "call_id": first.call_id, "output": self._content(parts)}
        )
        for call in rest:
            self.native.append(
                {"type": "function_call_output", "call_id": call.call_id, "output": SKIPPED_CALL_RESULT}
            )

    def _append_assistant_text(self, text):
        self.native.append({"role": "assistant", "content": text})

    async def _generate(self) -> AssistantReply:
        kwargs = {}
        if self.max_output_tokens is not None:
            kwargs["max_output_tokens"] = self.max_output_tokens

        response = await asyncio.to_thread(
            self.client.responses.create,
            model=self.model_name,
            instructions=self.system_prompt,
            input=list(self.native),
            tools=RESPONSES_TOOLS,
            parallel_tool_calls=False,
            # Summary of the model's reasoning, shown to the operator in the GUI.
            reasoning={"summary": "auto"},
            **kwargs,
        )
        logger.info(f"llm response (status={response.status}): {response.output_text!r}")

        calls = []
        reasoning = []
        for item in response.output:
            # Reasoning and function call items must be sent back with the next request.
            self.native.append(item.model_dump(exclude_none=True))
            if item.type == "function_call":
                calls.append(
                    ToolCall(name=item.name, arguments=_parse_arguments(item.arguments), call_id=item.call_id)
                )
            elif item.type == "reasoning":
                reasoning.extend(s.text for s in (item.summary or []))

        return AssistantReply(text=response.output_text or "", reasoning="\n".join(reasoning), tool_calls=calls)
