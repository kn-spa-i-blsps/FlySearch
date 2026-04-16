"""Unit tests for CommandRegistry and CommandDescriptor."""
import json
from unittest.mock import MagicMock

import pytest

from drone_control.command_registry import CommandDescriptor, CommandRegistry


def _make_descriptor(action="TEST_ACTION", send_immediate_ack=True, raise_on_call=False):
    def handler():
        if raise_on_call:
            raise RuntimeError("sensor failure")
        return {"value": 42}

    return CommandDescriptor(
        action=action,
        handler=handler,
        build_response=lambda data, seq: {"type": action, "seq": seq, **data},
        send_immediate_ack=send_immediate_ack,
    )


def _ws():
    ws = MagicMock()
    ws.sent = []
    ws.send.side_effect = lambda msg: ws.sent.append(msg)
    return ws


class TestRegistration:
    def test_register_and_get(self):
        registry = CommandRegistry()
        descriptor = _make_descriptor("GET_PHOTO_TELEMETRY")
        registry.register(descriptor)
        assert registry.get("GET_PHOTO_TELEMETRY") is descriptor

    def test_get_unknown_returns_none(self):
        assert CommandRegistry().get("NONEXISTENT") is None

    def test_actions_lists_registered_actions(self):
        registry = CommandRegistry()
        registry.register(_make_descriptor("ACTION_A"))
        registry.register(_make_descriptor("ACTION_B"))
        assert set(registry.actions()) == {"ACTION_A", "ACTION_B"}

    def test_register_overwrites_existing(self):
        registry = CommandRegistry()
        first = _make_descriptor("ACTION_A")
        second = _make_descriptor("ACTION_A")
        registry.register(first)
        registry.register(second)
        assert registry.get("ACTION_A") is second


class TestDispatch:
    def test_returns_false_for_unknown_action(self):
        registry = CommandRegistry()
        ws = _ws()
        assert registry.dispatch(ws, "UNKNOWN", seq=1) is False
        ws.send.assert_not_called()

    def test_returns_true_for_known_action(self):
        registry = CommandRegistry()
        registry.register(_make_descriptor("MY_ACTION"))
        ws = _ws()
        assert registry.dispatch(ws, "MY_ACTION", seq=1) is True

    def test_sends_immediate_ack_then_response(self):
        registry = CommandRegistry()
        registry.register(_make_descriptor("MY_ACTION", send_immediate_ack=True))
        ws = _ws()

        registry.dispatch(ws, "MY_ACTION", seq=5)

        assert len(ws.sent) == 2
        ack = json.loads(ws.sent[0])
        assert ack["type"] == "ACK"
        assert ack["ok"] is True
        assert ack["seq"] == 5

        response = json.loads(ws.sent[1])
        assert response["type"] == "MY_ACTION"
        assert response["value"] == 42

    def test_no_immediate_ack_when_disabled(self):
        registry = CommandRegistry()
        registry.register(_make_descriptor("MY_ACTION", send_immediate_ack=False))
        ws = _ws()

        registry.dispatch(ws, "MY_ACTION", seq=1)

        assert len(ws.sent) == 1
        response = json.loads(ws.sent[0])
        assert response["type"] == "MY_ACTION"

    def test_handler_error_sends_error_response_when_provided(self):
        registry = CommandRegistry()
        descriptor = CommandDescriptor(
            action="FAILING",
            handler=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            build_response=lambda data, seq: {"type": "FAILING"},
            send_immediate_ack=False,
            build_error_response=lambda exc, seq: {"type": "ACK", "ok": False, "error": str(exc)},
        )
        registry.register(descriptor)
        ws = _ws()

        registry.dispatch(ws, "FAILING", seq=1)

        assert len(ws.sent) == 1
        response = json.loads(ws.sent[0])
        assert response["ok"] is False
        assert "boom" in response["error"]

    def test_handler_error_silent_when_no_error_response(self):
        registry = CommandRegistry()
        registry.register(_make_descriptor("FAILING", raise_on_call=True))
        ws = _ws()

        # Should not raise; immediate ACK is sent but no data response
        registry.dispatch(ws, "FAILING", seq=1)

        assert len(ws.sent) == 1  # only the immediate ACK
        ack = json.loads(ws.sent[0])
        assert ack["type"] == "ACK"
