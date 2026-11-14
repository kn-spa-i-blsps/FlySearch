"""Heading compensation for VLM moves, done on the server.

The VLM gives moves in the camera frame of the photo it just saw:
dx = right in the image, dy = toward the top of the image, dz = up.

The drone (drone_control/utils/coords.py, MOVE_METHOD=1) executes a received
(x, y, z) in the WORLD frame as N = -y, E = -x, D = -z. That equals the
camera frame only when the drone faces north at photo time, and in practice
it does not: flight logs show the heading changing ~65 degrees between photos.

So the server rotates the camera-frame move by the heading recorded with the
photo and sends the drone the (x, y, z) that its fixed mapping turns into the
intended world displacement.

The camera is mounted with the top of the image toward the drone's tail
(CAMERA_ROTATION_DEG=180), confirmed by matching features between mission
photos against GPS displacement.
"""

import json
import math
import os
from pathlib import Path


def heading_compensation_enabled() -> bool:
    return os.environ.get("HEADING_COMPENSATION", "1").strip().lower() in ("1", "true", "yes", "on")


def camera_rotation_deg() -> float:
    """Clockwise angle from the drone's nose to the top of the image."""
    return float(os.environ.get("CAMERA_ROTATION_DEG", "180"))


def read_yaw(telemetry_path: Path) -> float | None:
    """Yaw in radians (0 = north, clockwise positive) from a saved telemetry file, or None."""
    try:
        data = json.loads(Path(telemetry_path).read_text()).get("data", {})
        yaw = (data.get("attitude") or {}).get("yaw")
        return None if yaw is None else float(yaw)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def camera_move_to_ned(
    move: tuple[float, float, float], yaw: float, cam_rot_deg: float
) -> tuple[float, float, float]:
    """Camera-frame (dx right, dy image-top, dz up) -> world (north, east, down) in meters."""
    dx, dy, dz = move
    # The image top points cam_rot_deg clockwise from the nose, so its world bearing
    # is yaw + cam_rot; image right is 90 degrees clockwise from that.
    top = yaw + math.radians(cam_rot_deg)
    north = dy * math.cos(top) - dx * math.sin(top)
    east = dy * math.sin(top) + dx * math.cos(top)
    return north, east, -dz


def to_drone_command(
    move: tuple[float, float, float], yaw: float | None, cam_rot_deg: float | None = None
) -> tuple[float, float, float]:
    """The (x, y, z) to send so the drone's fixed N=-y, E=-x, D=-z mapping flies `move`
    as seen in the photo taken at heading `yaw`. Returns `move` unchanged if yaw is unknown."""
    if yaw is None:
        return tuple(move)
    if cam_rot_deg is None:
        cam_rot_deg = camera_rotation_deg()
    north, east, down = camera_move_to_ned(move, yaw, cam_rot_deg)
    return (round(-east, 3) + 0.0, round(-north, 3) + 0.0, round(-down, 3) + 0.0)
