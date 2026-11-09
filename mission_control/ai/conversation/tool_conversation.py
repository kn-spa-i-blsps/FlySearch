import json
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from mission_control.ai.conversation.abstract_conversation import Conversation, Role

# Answer for every tool call beyond the first one in a single model turn - only one
# action is executed per step, but backends require a result for each call.
SKIPPED_CALL_RESULT = "Not executed: only one action is executed per step."


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str | None = None


@dataclass
class AssistantReply:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class ToolConversation(Conversation):
    """
    Conversation in the tool-calling format FlyLoRA was trained with.

    Subclasses keep the backend-native message list in ``self.native`` and implement
    the ``_append_*`` and ``_generate`` hooks. A user message sent while the previous
    assistant turn ended with a tool call becomes that call's result (image + text),
    exactly like a ``move`` observation in training.
    """

    def __init__(self):
        self.system_prompt: str | None = None
        self.native: list[Any] = []
        # (role, text | image) pairs for the GUI and chat storage.
        self.display: list[tuple[Role, Any]] = []
        self.pending_calls: list[ToolCall] = []
        self.last_reply: AssistantReply | None = None

        self.transaction_started = False
        self.transaction_role: Role | None = None
        self.transaction_parts: list[Any] = []

    # --- backend hooks ---

    def _append_user(self, parts: list[Any]) -> None:
        raise NotImplementedError

    def _append_tool_results(self, calls: list[ToolCall], parts: list[Any]) -> None:
        raise NotImplementedError

    def _append_assistant_text(self, text: str) -> None:
        raise NotImplementedError

    async def _generate(self) -> AssistantReply:
        """Sends ``self.native`` to the model, appends its turn to ``self.native``."""
        raise NotImplementedError

    # --- Conversation interface ---

    def begin_transaction(self, role: Role):
        if self.transaction_started:
            raise Exception("Transaction already started")

        self.transaction_started = True
        self.transaction_role = role
        self.transaction_parts = []

    def add_text_message(self, text: str):
        if not self.transaction_started:
            raise Exception("Transaction not started")

        self.transaction_parts.append(text)

    async def add_image_message(self, image: Image.Image):
        if not self.transaction_started:
            raise Exception("Transaction not started")

        self.transaction_parts.append(image.convert("RGB"))

    async def commit_transaction(self, send_to_vlm=False):
        if not self.transaction_started:
            raise Exception("Transaction not started")

        role = self.transaction_role
        parts = self.transaction_parts
        self.rollback_transaction()

        if role != Role.USER and send_to_vlm:
            raise Exception("Only user messages can be sent to the VLM")

        if role == Role.SYSTEM:
            self.system_prompt = "".join(p for p in parts if isinstance(p, str))
            self.display.append((Role.SYSTEM, self.system_prompt))
            return

        if role == Role.ASSISTANT:
            # Only used when restoring a saved chat; tool calls are kept as plain text.
            text = "".join(p for p in parts if isinstance(p, str))
            self._append_assistant_text(text)
            self.display.append((Role.ASSISTANT, text))
            return

        native_len = len(self.native)
        pending_calls = self.pending_calls
        try:
            if pending_calls:
                self._append_tool_results(pending_calls, parts)
            else:
                self._append_user(parts)
            self.pending_calls = []

            if send_to_vlm:
                reply = await self._generate()
        except Exception:
            del self.native[native_len:]
            self.pending_calls = pending_calls
            raise

        self.display.extend((Role.USER, p) for p in parts)
        if not send_to_vlm:
            return

        self.last_reply = reply
        self.pending_calls = reply.tool_calls
        self.display.append((Role.ASSISTANT, self._reply_to_text(reply)))

    def rollback_transaction(self):
        self.transaction_started = False
        self.transaction_role = None
        self.transaction_parts = []

    def get_conversation(self, save_urls=True):
        return list(self.display)

    def get_latest_message(self):
        if len(self.display) == 0:
            raise Exception("No messages in conversation")

        return self.display[-1]

    def get_entire_conversation(self):
        return self.native

    @staticmethod
    def _reply_to_text(reply: AssistantReply) -> str:
        lines = []
        if reply.reasoning:
            lines.append(f"<think>\n{reply.reasoning}\n</think>")
        if reply.text:
            lines.append(reply.text)
        for call in reply.tool_calls:
            lines.append(f"[tool call] {call.name}({json.dumps(call.arguments)})")
        return "\n".join(lines)
