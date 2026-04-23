"""Integration tests for MessageRouter — full message dispatch with mocked WS and sensors."""
import base64
import json
from unittest.mock import MagicMock
from pathlib import Path

import pytest

from drone_control.managers.command_manager import CommandManager
from drone_control.managers.message_router import MessageRouter
from drone_control.managers.session_log_manager import SessionLogManager
from drone_control.actuators.flight_controller import FlightController
from drone_control.core.runtime_context import RuntimeContext
from drone_control.command_registry import CommandDescriptor, CommandRegistry
from drone_control.sensors.recording_sensor import RecordingSensor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ws():
    ws = MagicMock()
    ws.sent = []
    ws.send.side_effect = lambda msg: ws.sent.append(msg)
    return ws


def _make_photo_sensor(photo_bytes=b"\xff\xd8\xff", fail=False):
    sensor = MagicMock()
    if fail:
        sensor.capture_bytes.side_effect = Exception("no camera")
    else:
        sensor.capture_bytes.return_value = photo_bytes
    return sensor


def _make_telem_sensor(data=None):
    sensor = MagicMock()
    sensor.snapshot.return_value = data or {"alt": 10.0}
    return sensor


def _make_recording_sensor():
    rec = MagicMock(spec=RecordingSensor)
    rec.start_recording.return_value = {"ok": True, "recording": True, "ref_count": 1}
    rec.stop_recording.return_value = {"ok": True, "recording": False, "ref_count": 0}
    rec.list_recordings.return_value = [{"name": "clip.mp4", "size_bytes": 1024}]
    return rec


def _make_router(tmp_path, photo_sensor=None, telem_sensor=None, fc_returns=True,
                 recording_sensor=None):
    photo = photo_sensor or _make_photo_sensor()
    telem = telem_sensor or _make_telem_sensor()
    rec = recording_sensor or _make_recording_sensor()

    ctx = RuntimeContext.from_commands_dir(tmp_path)
    fc = MagicMock(spec=FlightController)
    fc.maybe_execute_move.return_value = fc_returns
    command_mgr = CommandManager(logger=SessionLogManager(ctx), flight_controller=fc)

    registry = CommandRegistry()
    registry.register(CommandDescriptor(
        action="GET_PHOTO_TELEMETRY",
        handler=lambda: {
            "photo": _safe_capture(photo),
            "telemetry": telem.snapshot(),
        },
        build_response=lambda data, seq: {
            "type": "PHOTO_WITH_TELEMETRY",
            "photo": data.get("photo"),
            "telemetry": data.get("telemetry"),
            "seq": seq,
        },
        send_immediate_ack=True,
    ))
    registry.register(CommandDescriptor(
        action="START_RECORDING",
        handler=rec.start_recording,
        build_response=lambda status, seq: {
            "type": "ACK", "of": "RECORDING", "action": "START_RECORDING",
            "ok": bool(status.get("ok", True)),
            "recording": bool(status.get("recording", False)),
        },
        send_immediate_ack=False,
        build_error_response=lambda exc, seq: {
            "type": "ACK", "of": "RECORDING", "action": "START_RECORDING",
            "ok": False, "error": str(exc),
        },
    ))
    registry.register(CommandDescriptor(
        action="STOP_RECORDING",
        handler=rec.stop_recording,
        build_response=lambda status, seq: {
            "type": "ACK", "of": "RECORDING", "action": "STOP_RECORDING",
            "ok": bool(status.get("ok", True)),
            "recording": bool(status.get("recording", False)),
        },
        send_immediate_ack=False,
    ))
    registry.register(CommandDescriptor(
        action="GET_RECORDINGS",
        handler=rec.list_recordings,
        build_response=lambda recordings, seq: {
            "type": "ACK", "of": "RECORDINGS", "action": "GET_RECORDINGS",
            "ok": True, "count": len(recordings), "recordings": recordings,
        },
        send_immediate_ack=False,
    ))

    router = MessageRouter(
        command_manager=command_mgr,
        command_registry=registry,
        recording_sensor=rec,
    )
    return router, fc, rec


def _safe_capture(photo_sensor):
    try:
        return base64.b64encode(photo_sensor.capture_bytes()).decode("utf-8")
    except Exception:
        return None


def _sent_json(ws, index):
    return json.loads(ws.sent[index])


# ---------------------------------------------------------------------------
# GET_PHOTO_TELEMETRY
# ---------------------------------------------------------------------------

class TestGetPhotoTelemetry:
    def test_sends_immediate_ack_then_payload(self, tmp_path):
        router, _, _ = _make_router(tmp_path)
        ws = _ws()

        router.on_message(ws, json.dumps({"type": "COMMAND", "action": "GET_PHOTO_TELEMETRY", "seq": 5}))

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
        router, _, _ = _make_router(tmp_path, photo_sensor=_make_photo_sensor(fail=True))
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
        router, fc, _ = _make_router(tmp_path, fc_returns=True)
        ws = _ws()

        router.on_message(ws, json.dumps({"type": "COMMAND", "action": "MOVE", "move": [1, 0, 0], "seq": 10}))

        assert len(ws.sent) == 2
        ack = _sent_json(ws, 0)
        assert ack["type"] == "ACK"
        assert ack["action"] == "MOVE"
        assert ack["seq"] == 10

        executed_msg = _sent_json(ws, 1)
        assert executed_msg["type"] == "MOVE_EXECUTED"
        assert executed_msg["seq"] == 10
        assert executed_msg["ok"] is True

    def test_move_executed_sent_even_when_fc_raises(self, tmp_path):
        router, fc, _ = _make_router(tmp_path)
        fc.maybe_execute_move.side_effect = RuntimeError("FC crash")
        ws = _ws()

        router.on_message(ws, json.dumps({"type": "COMMAND", "action": "MOVE", "move": [0, 1, 0], "seq": 3}))

        executed_msg = _sent_json(ws, 1)
        assert executed_msg["type"] == "MOVE_EXECUTED"
        assert executed_msg["ok"] is False

    def test_move_calls_flight_controller(self, tmp_path):
        router, fc, _ = _make_router(tmp_path)
        ws = _ws()

        router.on_message(ws, json.dumps({"type": "COMMAND", "action": "MOVE", "move": [2.0, 3.0, 4.0], "seq": 1}))

        fc.maybe_execute_move.assert_called_once_with((2.0, 3.0, 4.0))


# ---------------------------------------------------------------------------
# Recording commands
# ---------------------------------------------------------------------------

class TestRecordingCommands:
    def test_start_recording_ack(self, tmp_path):
        router, _, _ = _make_router(tmp_path)
        ws = _ws()

        router.on_message(ws, json.dumps({"type": "COMMAND", "action": "START_RECORDING"}))

        assert len(ws.sent) == 1
        ack = _sent_json(ws, 0)
        assert ack["of"] == "RECORDING"
        assert ack["action"] == "START_RECORDING"
        assert ack["ok"] is True
        assert ack["recording"] is True

    def test_stop_recording_ack(self, tmp_path):
        router, _, _ = _make_router(tmp_path)
        ws = _ws()

        router.on_message(ws, json.dumps({"type": "COMMAND", "action": "STOP_RECORDING"}))

        ack = _sent_json(ws, 0)
        assert ack["action"] == "STOP_RECORDING"
        assert ack["recording"] is False

    def test_get_recordings_ack(self, tmp_path):
        router, _, _ = _make_router(tmp_path)
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
        router, _, _ = _make_router(tmp_path)
        ws = _ws()

        router.on_message(ws, json.dumps({"type": "ACK", "of": "PHOTO_WITH_TELEMETRY", "ok": True, "seq": 5}))

        ws.send.assert_not_called()


# ---------------------------------------------------------------------------
# Unknown / unrecognized messages
# ---------------------------------------------------------------------------

class TestUnrecognizedMessages:
    def test_unknown_text_sends_error(self, tmp_path):
        router, _, _ = _make_router(tmp_path)
        ws = _ws()

        router.on_message(ws, "UNKNOWN_COMMAND")

        assert ws.send.called
        sent = json.loads(ws.sent[0])
        assert sent["type"] == "ERROR"
        assert "invalid" in sent["message"].lower()
