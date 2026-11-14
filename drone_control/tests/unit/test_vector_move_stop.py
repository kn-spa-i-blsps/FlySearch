from types import SimpleNamespace

import pytest

from drone_control.actuators import pixhawk_vector_backend as backend


class FakeMav:
    def __init__(self):
        self.sent = []

    def set_position_target_local_ned_send(self, *args):
        self.sent.append(args)


def test_velocity_move_ends_with_zero_velocity(monkeypatch):
    if backend.mavutil is None:
        frames = SimpleNamespace(MAV_FRAME_LOCAL_NED=1, MAV_FRAME_BODY_NED=8)
        monkeypatch.setattr(backend, "mavutil", SimpleNamespace(mavlink=frames))
    monkeypatch.setattr(backend.time, "sleep", lambda s: None)
    clock = iter(i * 0.1 for i in range(10_000))
    monkeypatch.setattr(backend.time, "time", lambda: next(clock))
    master = SimpleNamespace(mav=FakeMav(), target_system=1, target_component=1)

    assert backend.send_vector_command_via(master, vector=(2.0, 0.0, 0.0), method_id=1)

    velocities = [args[8:11] for args in master.mav.sent]
    assert velocities[0] == pytest.approx((1.0, 0.0, 0.0))
    # The last setpoints the FC sees must be zero, or it keeps flying for 3 s.
    assert velocities[-1] == (0, 0, 0)
    assert velocities.count((0, 0, 0)) >= 3
