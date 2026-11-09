import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from mission_control.ai.conversation.abstract_conversation import Role
from mission_control.ai.conversation.gemini.gemini_conversation import (
    GeminiConversation,
)
from mission_control.ai.conversation.openai.openai_conversation import (
    OpenAIChatConversation,
)
from mission_control.ai.conversation.openai.openai_responses_conversation import (
    OpenAIResponsesConversation,
)
from mission_control.ai.conversation.tool_conversation import (
    SKIPPED_CALL_RESULT,
    ToolCall,
)
from mission_control.ai.prompt_helpers.training_prompt import TOOLS


def image():
    return Image.new("RGB", (8, 8), "red")


def send(conversation, parts, role=Role.USER, send_to_vlm=True):
    async def go():
        conversation.begin_transaction(role)
        for part in parts:
            if isinstance(part, str):
                conversation.add_text_message(part)
            else:
                await conversation.add_image_message(part)
        await conversation.commit_transaction(send_to_vlm=send_to_vlm)

    asyncio.run(go())


# --- vLLM / Chat Completions ---


def chat_response(content="", reasoning=None, calls=()):
    tool_calls = [
        SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))
        for i, (name, args) in enumerate(calls)
    ]
    message = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=tool_calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")])


def chat_conversation(*responses):
    client = MagicMock()
    client.chat.completions.create.side_effect = list(responses)
    conversation = OpenAIChatConversation(client, "flylora", max_tokens=100)
    send(conversation, ["system prompt"], role=Role.SYSTEM, send_to_vlm=False)
    return conversation, client.chat.completions.create


class TestOpenAIChatConversation:
    def test_first_observation_is_a_user_message_after_the_system_prompt(self):
        conversation, create = chat_conversation(chat_response(calls=[("move", {"dx": 1, "dy": 2, "dz": -3})]))

        send(conversation, ["intro", image(), "Task: Find helipad"])

        messages = create.call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "system prompt"}
        assert messages[1]["role"] == "user"
        assert [p["type"] for p in messages[1]["content"]] == ["text", "image_url", "text"]
        assert conversation.last_reply.tool_calls == [ToolCall("move", {"dx": 1, "dy": 2, "dz": -3}, "call_0")]

    def test_next_observation_is_the_result_of_the_pending_tool_call(self):
        conversation, create = chat_conversation(
            chat_response(reasoning="thinking", calls=[("move", {"dx": 0, "dy": 0, "dz": -5})]),
            chat_response(calls=[("found", {})]),
        )

        send(conversation, ["intro", image(), "Task"])
        send(conversation, [image(), "Movement successful."])

        messages = create.call_args.kwargs["messages"]
        assistant, tool = messages[2], messages[3]
        assert assistant["reasoning"] == "thinking"
        assert assistant["tool_calls"][0]["id"] == "call_0"
        assert tool["role"] == "tool"
        assert tool["tool_call_id"] == "call_0"
        assert [p["type"] for p in tool["content"]] == ["image_url", "text"]
        assert conversation.last_reply.tool_calls[0].name == "found"

    def test_extra_tool_calls_get_a_skipped_result(self):
        conversation, create = chat_conversation(
            chat_response(calls=[("move", {"dx": 1, "dy": 0, "dz": 0}), ("found", {})]),
            chat_response(calls=[("found", {})]),
        )

        send(conversation, ["intro", image(), "Task"])
        send(conversation, [image(), "Movement successful."])

        messages = create.call_args.kwargs["messages"]
        assert messages[4] == {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "found",
            "content": SKIPPED_CALL_RESULT,
        }

    def test_exact_training_tool_schemas_go_to_the_chat_template(self):
        conversation, create = chat_conversation(chat_response(calls=[("found", {})]))

        send(conversation, ["intro", image(), "Task"])

        kwargs = create.call_args.kwargs
        assert kwargs["extra_body"]["chat_template_kwargs"]["tools"] == TOOLS
        assert all("return" not in t["function"] for t in kwargs["tools"])

    def test_failed_request_rolls_back_and_keeps_the_pending_call(self):
        conversation, create = chat_conversation(
            chat_response(calls=[("move", {"dx": 1, "dy": 0, "dz": 0})]),
            RuntimeError("timeout"),
            chat_response(calls=[("found", {})]),
        )
        send(conversation, ["intro", image(), "Task"])

        with pytest.raises(RuntimeError):
            send(conversation, [image(), "Movement successful."])
        send(conversation, [image(), "Movement successful."])

        roles = [m["role"] for m in create.call_args.kwargs["messages"]]
        assert roles == ["system", "user", "assistant", "tool"]

    def test_display_history_has_text_and_images_for_storage(self):
        conversation, _ = chat_conversation(chat_response(calls=[("found", {})]))

        send(conversation, ["intro", image(), "Task"])

        history = conversation.get_conversation()
        assert [role for role, _ in history] == [Role.SYSTEM, Role.USER, Role.USER, Role.USER, Role.ASSISTANT]
        assert isinstance(history[2][1], Image.Image)
        assert history[-1][1] == "[tool call] found({})"


# --- OpenAI Responses API ---


def responses_response(calls=(), summary=()):
    output = []
    if summary:
        item = MagicMock(type="reasoning", summary=[SimpleNamespace(text=s) for s in summary])
        item.model_dump.return_value = {"type": "reasoning", "id": "rs_1"}
        output.append(item)
    for i, (name, args) in enumerate(calls):
        item = MagicMock(type="function_call", call_id=f"fc_{i}", arguments=json.dumps(args))
        item.name = name
        item.model_dump.return_value = {"type": "function_call", "call_id": f"fc_{i}", "name": name}
        output.append(item)
    return SimpleNamespace(output=output, output_text="", status="completed")


class TestOpenAIResponsesConversation:
    def test_tool_result_is_sent_as_function_call_output_with_image(self):
        client = MagicMock()
        client.responses.create.side_effect = [
            responses_response(calls=[("move", {"dx": 0, "dy": 5, "dz": 0})], summary=["looking"]),
            responses_response(calls=[("found", {})]),
        ]
        conversation = OpenAIResponsesConversation(client, "gpt-6-luna")
        send(conversation, ["system prompt"], role=Role.SYSTEM, send_to_vlm=False)

        send(conversation, ["intro", image(), "Task"])
        assert conversation.last_reply.reasoning == "looking"
        send(conversation, [image(), "Movement successful."])

        kwargs = client.responses.create.call_args.kwargs
        assert kwargs["instructions"] == "system prompt"
        items = kwargs["input"]
        assert [i.get("type", i.get("role")) for i in items] == ["user", "reasoning", "function_call", "function_call_output"]
        assert items[3]["call_id"] == "fc_0"
        assert [p["type"] for p in items[3]["output"]] == ["input_image", "input_text"]


# --- Gemini ---


def gemini_part(text=None, thought=False, call=None):
    function_call = SimpleNamespace(name=call[0], args=call[1], id=None) if call else None
    return SimpleNamespace(text=text, thought=thought, function_call=function_call)


def gemini_response(*parts):
    content = SimpleNamespace(role="model", parts=list(parts))
    return SimpleNamespace(candidates=[SimpleNamespace(content=content, finish_reason="STOP")])


class TestGeminiConversation:
    def make(self, *responses):
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(side_effect=list(responses))
        conversation = GeminiConversation(client, "gemini-test", retry_delay=0)
        send(conversation, ["system prompt"], role=Role.SYSTEM, send_to_vlm=False)
        return conversation, client.aio.models.generate_content

    def test_move_result_is_a_function_response_plus_image(self):
        conversation, generate = self.make(
            gemini_response(gemini_part("hmm", thought=True), gemini_part(call=("move", {"dx": 1, "dy": 2, "dz": 3}))),
            gemini_response(gemini_part(call=("found", {}))),
        )

        send(conversation, ["intro", image(), "Task"])
        assert conversation.last_reply.reasoning == "hmm"
        send(conversation, [image(), "Movement successful."])

        contents = generate.call_args.kwargs["contents"]
        tool_turn = contents[2]
        assert tool_turn.parts[0].function_response.name == "move"
        assert tool_turn.parts[0].function_response.response == {"result": "Movement successful."}
        assert tool_turn.parts[1].inline_data is not None
        assert generate.call_args.kwargs["config"].system_instruction == "system prompt"

    def test_retries_on_overloaded_model(self):
        from google.genai.errors import ServerError

        overloaded = ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
        conversation, generate = self.make(overloaded, gemini_response(gemini_part(call=("found", {}))))

        send(conversation, ["intro", image(), "Task"])

        assert generate.call_count == 2
        assert conversation.last_reply.tool_calls[0].name == "found"
