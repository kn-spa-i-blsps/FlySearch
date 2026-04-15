"""Unit tests for AcquisitionManager sensor data fetching."""
import base64
from unittest.mock import MagicMock, patch

import pytest

from drone_control.managers.acquisition_manager import AcquisitionManager


def _make_manager(photo_bytes=b"JPEG", telemetry=None, recording_sensor=None):
    photo_sensor = MagicMock()
    photo_sensor.capture_bytes.return_value = photo_bytes

    telem_sensor = MagicMock()
    telem_sensor.snapshot.return_value = telemetry or {"alt": 20.0, "lat": 52.0}

    rec_sensor = recording_sensor or MagicMock()

    return AcquisitionManager(
        photo_sensor=photo_sensor,
        telemetry_sensor=telem_sensor,
        recording_sensor=rec_sensor,
    )


class TestBuildPhotoWithTelemetry:
    def test_photo_is_base64_encoded(self):
        raw = b"\xff\xd8\xff"  # JPEG magic bytes
        mgr = _make_manager(photo_bytes=raw)
        payload = mgr.build_photo_with_telemetry()
        assert payload["photo"] == base64.b64encode(raw).decode("utf-8")

    def test_telemetry_is_included(self):
        telem = {"alt": 50.0, "lat": 52.1, "lon": 21.0}
        mgr = _make_manager(telemetry=telem)
        payload = mgr.build_photo_with_telemetry()
        assert payload["telemetry"] == telem

    def test_photo_none_when_sensor_raises(self):
        mgr = _make_manager()
        mgr.photo_sensor.capture_bytes.side_effect = RuntimeError("camera unavailable")
        payload = mgr.build_photo_with_telemetry()
        assert payload["photo"] is None

    def test_telemetry_still_present_when_photo_fails(self):
        telem = {"alt": 10.0}
        mgr = _make_manager(telemetry=telem)
        mgr.photo_sensor.capture_bytes.side_effect = Exception("boom")
        payload = mgr.build_photo_with_telemetry()
        assert payload["telemetry"] == telem

    def test_payload_type(self):
        mgr = _make_manager()
        payload = mgr.build_photo_with_telemetry()
        assert payload["type"] == "PHOTO_WITH_TELEMETRY"


class TestRecordingDelegation:
    def test_start_recording_delegates(self):
        rec = MagicMock()
        rec.start_recording.return_value = {"ok": True, "recording": True}
        mgr = _make_manager(recording_sensor=rec)
        result = mgr.start_recording()
        rec.start_recording.assert_called_once()
        assert result["ok"] is True

    def test_stop_recording_delegates(self):
        rec = MagicMock()
        rec.stop_recording.return_value = {"ok": True, "recording": False}
        mgr = _make_manager(recording_sensor=rec)
        result = mgr.stop_recording()
        rec.stop_recording.assert_called_once()
        assert result["recording"] is False

    def test_list_recordings_delegates(self):
        rec = MagicMock()
        rec.list_recordings.return_value = [{"name": "clip.mp4"}]
        mgr = _make_manager(recording_sensor=rec)
        result = mgr.list_recordings()
        assert result == [{"name": "clip.mp4"}]
