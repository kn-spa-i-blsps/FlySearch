"""Integration tests for MessageRouter — full message dispatch with mocked WS and sensors."""
import json
from unittest.mock import MagicMock, call, patch
from pathlib import Path

import pytest

from drone_control.managers.acquisition_manager import AcquisitionManager
from drone_control.managers.command_manager import CommandManager
from drone_control.managers.message_router import MessageRouter
from drone_control.managers.session_log_manager import SessionLogManager
from drone_control.actuators.flight_controller import FlightController
from drone_control.core.runtime_context import RuntimeContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ws():
    """Return a mock WebSocketApp that records sent messages."""
    ws = MagicMock()
    ws.sent = []
    ws.send.side_effect = lambda msg: ws.sent.append(msg)
    return ws


def _make_router(tmp_path, fc_returns=True):
    photo_sensor = MagicMock()
    photo_sensor.capture_bytes.return_value = b"\xff\xd8\xff"

    telem_sensor = MagicMock()
    telem_sensor.snapshot.return_value = {"alt": 10.0}

    rec_sensor = MagicMock()
    rec_sensor.start_recording.return_value = {"ok": True, "recording": True, "ref_count": 1}
    rec_sensor.stop_recording.return_value = {"ok": True, "recording": False, "ref_count": 0}
    rec_sensor.list_recordings.return_value = [{"name": "clip.mp4", "size_bytes": 1024}]

    acquisition = AcquisitionManager(
        photo_sensor=photo_sensor,
        telemetry_sensor=telem_sensor,
        recording_sensor=rec_sensor,
    )

    ctx = RuntimeContext.from_commands_dir(tmp_path)
    logger = SessionLogManager(ctx)
    fc = MagicMock(spec=FlightController)
    fc.maybe_execute_move.return_value = fc_returns
    command_mgr = CommandManager(logger=logger, flight_controller=fc)

    router = MessageRouter(acquisition=acquisition, command_manager=command_mgr)
    return router, fc


def _sent_json(ws, index):
    return json.loads(ws.sent[index])


# ---------------------------------------------------------------------------
# GET_PHOTO_TELEMETRY
# ---------------------------------------------------------------------------

class TestGetPhotoTelemetry:
    def test_sends_immediate_ack_then_payload(self, tmp_path):
        router, _ = _make_router(tmp_path)
        ws = _ws()
        msg = json.dumps({"type": "COMMAND", "action": "GET_PHOTO_TELEMETRY", "seq": 5})

        router.on_message(ws, msg)

        assert len(ws.sent) == 2
        ack = _sent_json(ws, 0)
        assert ack["type"] == "ACK"
        assert ack["ok"] is True
        assert ack["seq"] == 5

        photo_msg = _sent_json(ws, 1)
        assert photo_msg["type"] == "PHOTO_WITH_TELEMETRY"
        assert photo_msg["seq"] == 5
        assert photo_msg["photo"] is not None
        assert photo_msg["telemetry"] == {"alt": 10.0}

    def test_photo_none_when_sensor_fails(self, tmp_path):
        router, _ = _make_router(tmp_path)
        router.acquisition.photo_sensor.capture_bytes.side_effect = Exception("no camera")
        ws = _ws()

        router.on_message(ws, json.dumps({"type": "COMMAND", "action": "GET_PHOTO_TELEMETRY", "seq": 1}))

        photo_msg = _sent_json(ws, 1)
        assert photo_msg["photo"] is None
        assert photo_msg["telemetry"] is not None


# ---------------------------------------------------------------------------
# MOVE command
# ---------------------------------------------------------------------------

class TestMoveCommand:
    def test_sends_immediate_ack_then_move_executed(self, tmp_path):
        router, fc = _make_router(tmp_path, fc_returns=True)
        ws = _ws()
        msg = json.dumps({"type": "COMMAND", "action": "MOVE", "move": [1, 0, 0], "seq": 10})

        router.on_message(ws, msg)

        assert len(ws.sent) == 2
        ack = _sent_json(ws, 0)
        assert ack["type"] == "ACK"
        assert ack["action"] == "MOVE"
        assert ack["ok"] is True
        assert ack["seq"] == 10

        executed_msg = _sent_json(ws, 1)
        assert executed_msg["type"] == "MOVE_EXECUTED"
        assert executed_msg["seq"] == 10
        assert executed_msg["ok"] is True

    def test_move_executed_sent_even_when_fc_raises(self, tmp_path):
        router, fc = _make_router(tmp_path)
        fc.maybe_execute_move.side_effect = RuntimeError("FC crash")
        ws = _ws()

        router.on_message(ws, json.dumps({"type": "COMMAND", "action": "MOVE", "move": [0, 1, 0], "seq": 3}))

        assert len(ws.sent) == 2
        executed_msg = _sent_json(ws, 1)
        assert executed_msg["type"] == "MOVE_EXECUTED"
        assert executed_msg["ok"] is False

    def test_move_calls_flight_controller(self, tmp_path):
        router, fc = _make_router(tmp_path)
        ws = _ws()

        router.on_message(ws, json.dumps({"type": "COMMAND", "action": "MOVE", "move": [2.0, 3.0, 4.0], "seq": 1}))

        fc.maybe_execute_move.assert_called_once_with((2.0, 3.0, 4.0))


# ---------------------------------------------------------------------------
# Recording commands
# ---------------------------------------------------------------------------

class TestRecordingCommands:
    def test_start_recording_ack(self, tmp_path):
        router, _ = _make_router(tmp_path)
        ws = _ws()

        router.on_message(ws, json.dumps({"type": "COMMAND", "action": "START_RECORDING"}))

        assert len(ws.sent) == 1
        ack = _sent_json(ws, 0)
        assert ack["of"] == "RECORDING"
        assert ack["action"] == "START_RECORDING"
        assert ack["ok"] is True
        assert ack["recording"] is True

    def test_stop_recording_ack(self, tmp_path):
        router, _ = _make_router(tmp_path)
        ws = _ws()

        router.on_message(ws, json.dumps({"type": "COMMAND", "action": "STOP_RECORDING"}))

        ack = _sent_json(ws, 0)
        assert ack["action"] == "STOP_RECORDING"
        assert ack["recording"] is False

    def test_get_recordings_ack(self, tmp_path):
        router, _ = _make_router(tmp_path)
        ws = _ws()

        router.on_message(ws, json.dumps({"type": "COMMAND", "action": "GET_RECORDINGS"}))

        ack = _sent_json(ws, 0)
        assert ack["action"] == "GET_RECORDINGS"
        assert ack["count"] == 1
        assert ack["recordings"][0]["name"] == "clip.mp4"


# ---------------------------------------------------------------------------
# Server ACKs (drone should log and not respond)
# ---------------------------------------------------------------------------

class TestServerAckHandling:
    def test_server_ack_does_not_trigger_response(self, tmp_path):
        router, _ = _make_router(tmp_path)
        ws = _ws()
        server_ack = json.dumps({"type": "ACK", "of": "PHOTO_WITH_TELEMETRY", "ok": True, "seq": 5})

        router.on_message(ws, server_ack)

        ws.send.assert_not_called()


# ---------------------------------------------------------------------------
# Unknown / unrecognized messages
# ---------------------------------------------------------------------------

class TestUnrecognizedMessages:
    def test_unknown_text_sends_error(self, tmp_path):
        router, _ = _make_router(tmp_path)
        ws = _ws()

        router.on_message(ws, "UNKNOWN_COMMAND")

        assert ws.send.called
        sent = ws.sent[0]
        assert "invalid" in sent.lower() or "SEND_PHOTO" in sent or "Accepted" in sent
