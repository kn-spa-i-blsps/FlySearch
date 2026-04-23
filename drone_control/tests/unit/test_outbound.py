"""Unit tests for outbound payload builders."""
import pytest

from drone_control.protocols.outbound import build_command_ack, build_photo_with_telemetry_payload


class TestBuildCommandAck:
    def test_ok_ack_has_required_fields(self):
        ack = build_command_ack(seq=1, ok=True)
        assert ack["type"] == "ACK"
        assert ack["of"] == "COMMAND"
        assert ack["ok"] is True
        assert ack["seq"] == 1

    def test_ok_ack_includes_executed_flag(self):
        ack = build_command_ack(seq=1, ok=True, executed=True)
        assert ack["executed"] is True

    def test_ok_ack_executed_defaults_false(self):
        ack = build_command_ack(seq=1, ok=True)
        assert ack["executed"] is False

    def test_ok_ack_with_action(self):
        ack = build_command_ack(seq=2, ok=True, action="MOVE")
        assert ack["action"] == "MOVE"

    def test_error_ack_has_error_field(self):
        ack = build_command_ack(seq=3, ok=False, error="something went wrong")
        assert ack["ok"] is False
        assert ack["error"] == "something went wrong"
        assert "executed" not in ack

    def test_error_ack_with_action(self):
        ack = build_command_ack(seq=3, ok=False, action="FOUND", error="oops")
        assert ack["action"] == "FOUND"

    def test_seq_none_omits_seq_field(self):
        ack = build_command_ack(seq=None, ok=True)
        assert "seq" not in ack

    def test_action_none_omits_action_field(self):
        ack = build_command_ack(seq=1, ok=True, action=None)
        assert "action" not in ack

    def test_error_field_omitted_on_success(self):
        ack = build_command_ack(seq=1, ok=True, error="should not appear")
        assert "error" not in ack


class TestBuildPhotoWithTelemetryPayload:
    def test_structure(self):
        telem = {"alt": 10.0}
        payload = build_photo_with_telemetry_payload(photo_base64="abc123", telemetry=telem)
        assert payload["type"] == "PHOTO_WITH_TELEMETRY"
        assert payload["photo"] == "abc123"
        assert payload["telemetry"] == telem

    def test_photo_can_be_none(self):
        payload = build_photo_with_telemetry_payload(photo_base64=None, telemetry={})
        assert payload["photo"] is None
