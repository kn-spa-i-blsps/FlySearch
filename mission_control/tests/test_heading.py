import json
import math

import pytest

from drone_control.utils.coords import grid_xyz_to_ned
from mission_control.utils.heading import camera_move_to_ned, read_yaw, to_drone_command


def _flown_ned(move, yaw, cam_rot=180):
    """World displacement the drone ends up flying for a VLM move, server + drone combined."""
    return grid_xyz_to_ned(to_drone_command(move, yaw, cam_rot))


def test_facing_north_is_unchanged():
    # At yaw 0 the drone's existing mapping was already right: nothing to correct.
    assert to_drone_command((7.0, -3.0, 2.0), 0.0, 180) == pytest.approx((7.0, -3.0, 2.0))


def test_unknown_yaw_passes_move_through():
    assert to_drone_command((1.0, 2.0, 3.0), None, 180) == (1.0, 2.0, 3.0)


@pytest.mark.parametrize("yaw_deg", [-158, -71, 0, 45, 90, 136, 180])
def test_drone_flies_where_the_image_points(yaw_deg):
    yaw = math.radians(yaw_deg)
    # Camera top = drone tail, so "up in the image" is the direction opposite the heading.
    n, e, d = _flown_ned((0.0, 10.0, 0.0), yaw)
    assert (n, e) == pytest.approx((-10 * math.cos(yaw), -10 * math.sin(yaw)), abs=0.01)
    # "Right in the image" is 90 degrees clockwise from image-up.
    n, e, d = _flown_ned((10.0, 0.0, 0.0), yaw)
    assert (n, e) == pytest.approx((10 * math.sin(yaw), -10 * math.cos(yaw)), abs=0.01)


def test_vertical_component_is_kept():
    assert _flown_ned((0.0, 0.0, 5.0), math.radians(77))[2] == pytest.approx(-5.0)


def test_luna01_step5_now_goes_east_instead_of_west():
    # Real flight: yaw -158 deg, VLM asked 7 m right; the uncompensated drone flew 9.8 m WEST,
    # while the photos showed that heading put image-right roughly east.
    n, e, _ = _flown_ned((7.0, 0.0, 0.0), math.radians(-158))
    assert e > 6.0 and n < 0


def test_camera_move_to_ned_with_nose_up_camera():
    n, e, d = camera_move_to_ned((0.0, 5.0, 1.0), math.radians(90), 0)
    assert (n, e, d) == pytest.approx((0.0, 5.0, -1.0), abs=1e-9)


def test_read_yaw(tmp_path):
    f = tmp_path / "t.json"
    f.write_text(json.dumps({"data": {"attitude": {"yaw": -1.24}}}))
    assert read_yaw(f) == pytest.approx(-1.24)
    f.write_text(json.dumps({"data": {"position": {"alt": 3}}}))
    assert read_yaw(f) is None
    assert read_yaw(tmp_path / "missing.json") is None
