"""Unit tests for inbound message parsing."""
import json

import pytest

from drone_control.protocols.inbound import IN_COMMAND, parse_inbound_message


def test_start_recording_json():
    msg = parse_inbound_message(json.dumps({"type": "COMMAND", "action": "START_RECORDING"}))
    assert msg.kind == IN_COMMAND
    assert msg.json_obj["action"] == "START_RECORDING"


def test_stop_recording_json():
    msg = parse_inbound_message(json.dumps({"type": "COMMAND", "action": "STOP_RECORDING"}))
    assert msg.kind == IN_COMMAND
    assert msg.json_obj["action"] == "STOP_RECORDING"


def test_get_recordings_json():
    msg = parse_inbound_message(json.dumps({"type": "COMMAND", "action": "GET_RECORDINGS"}))
    assert msg.kind == IN_COMMAND
    assert msg.json_obj["action"] == "GET_RECORDINGS"


def test_get_photo_telemetry_json():
    raw = json.dumps({"type": "COMMAND", "action": "GET_PHOTO_TELEMETRY", "seq": 7})
    msg = parse_inbound_message(raw)
    assert msg.kind == IN_COMMAND
    assert msg.json_obj["action"] == "GET_PHOTO_TELEMETRY"
    assert msg.json_obj["seq"] == 7


def test_pull_recordings_json():
    raw = json.dumps({"type": "COMMAND", "action": "PULL_RECORDINGS", "names": ["a.mp4"]})
    msg = parse_inbound_message(raw)
    assert msg.kind == IN_COMMAND
    assert msg.json_obj["action"] == "PULL_RECORDINGS"
    assert msg.json_obj["names"] == ["a.mp4"]


def test_move_command_json():
    raw = json.dumps({"type": "COMMAND", "action": "MOVE", "move": [1, 0, 0], "seq": 3})
    msg = parse_inbound_message(raw)
    assert msg.kind == IN_COMMAND
    assert msg.json_obj["action"] == "MOVE"


def test_found_command_json():
    raw = json.dumps({"type": "COMMAND", "action": "FOUND", "seq": 5})
    msg = parse_inbound_message(raw)
    assert msg.kind == IN_COMMAND
    assert msg.json_obj["action"] == "FOUND"


def test_server_ack_is_json_kind():
    raw = json.dumps({"type": "ACK", "of": "COMMAND", "ok": True, "seq": 1})
    msg = parse_inbound_message(raw)
    assert msg.kind == "JSON"
    assert msg.json_obj["type"] == "ACK"


def test_unknown_plain_text():
    msg = parse_inbound_message("HELLO_WORLD")
    assert msg.kind == "TEXT"
    assert msg.json_obj is None


def test_non_text_binary():
    msg = parse_inbound_message(b"\x00\x01\x02")
    assert msg.kind == "NON_TEXT"


def test_malformed_json_falls_back_to_text():
    msg = parse_inbound_message("{not valid json}")
    assert msg.kind == "TEXT"
