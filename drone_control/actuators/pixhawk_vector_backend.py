import math
import time
from typing import Any

try:
    from pymavlink import mavutil  # type: ignore
except Exception as exc:  # pragma: no cover - depends on runtime image
    mavutil = None
    _MAV_IMPORT_ERROR = exc
else:
    _MAV_IMPORT_ERROR = None

DEFAULT_SPEED = 1.0
DEFAULT_VEL_SEND_RATE_HZ = 5.0
DEFAULT_ACCEL_MAG = 0.5
DEFAULT_ACCEL_SEND_RATE_HZ = 5.0


def _connect(device: str, baud: int, heartbeat_timeout: float = 5.0) -> Any:
    if mavutil is None:
        raise RuntimeError(f"pymavlink unavailable: {_MAV_IMPORT_ERROR}")
    master = mavutil.mavlink_connection(device, baud=baud)
    master.wait_heartbeat(timeout=heartbeat_timeout)
    return master


def _get_mode(master: Any) -> str:
    hb = master.recv_match(type="HEARTBEAT", blocking=True, timeout=1.0)
    if hb is None:
        hb = master.messages.get("HEARTBEAT")
        if hb is None:
            return "UNKNOWN"

    try:
        return mavutil.mode_string_v10(hb)
    except Exception:
        return "UNKNOWN"


def _is_guided(master: Any) -> bool:
    mode = _get_mode(master)
    print(f"[vector_move] Current mode: {mode}")
    return mode == "GUIDED"


def _method_position_offset(master: Any, dx: float, dy: float, dz: float) -> bool:
    type_mask = 0b0000111111111000
    try:
        master.mav.set_position_target_local_ned_send(
            0,
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_FRAME_LOCAL_OFFSET_NED,
            type_mask,
            dx,
            dy,
            dz,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        print(f"[M0] Sent offset (dx={dx}, dy={dy}, dz={dz}) [m, NED]")
        return True
    except Exception as exc:
        print("[M0] Error:", exc)
        return False


def _method_velocity_ned(master: Any, dx: float, dy: float, dz: float) -> bool:
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dist < 1e-3:
        print("[M1] Vector ~0, nothing to do.")
        return True

    speed = max(DEFAULT_SPEED, 0.01)
    duration = dist / speed
    vx = dx / duration
    vy = dy / duration
    vz = dz / duration

    type_mask = 3527
    dt = 1 / DEFAULT_VEL_SEND_RATE_HZ if DEFAULT_VEL_SEND_RATE_HZ > 0 else 0.2
    t0 = time.time()

    print(
        f"[M1] move by NED offset (dx={dx}, dy={dy}, dz={dz}) [m] "
        f"-> vNED=(vx={vx:.2f}, vy={vy:.2f}, vz={vz:.2f}) [m/s], duration={duration:.2f}s"
    )

    while time.time() - t0 < duration:
        try:
            master.mav.set_position_target_local_ned_send(
                0,
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_FRAME_LOCAL_NED,
                type_mask,
                0,
                0,
                0,
                vx,
                vy,
                vz,
                0,
                0,
                0,
                0,
                0,
            )
        except Exception as exc:
            print("[M1] Error:", exc)
            return False
        time.sleep(dt)

    return True


def _method_velocity_body(master: Any, dx: float, dy: float, dz: float) -> bool:
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dist < 1e-3:
        print("[M2] Vector ~0, nothing to do.")
        return True

    speed = max(DEFAULT_SPEED, 0.01)
    duration = dist / speed
    vx = dx / duration
    vy = dy / duration
    vz = dz / duration

    type_mask = 3527
    dt = 1 / DEFAULT_VEL_SEND_RATE_HZ if DEFAULT_VEL_SEND_RATE_HZ > 0 else 0.2
    t0 = time.time()

    print(
        f"[M2] move by BODY offset (dx={dx}, dy={dy}, dz={dz}) [m in body frame] "
        f"-> vBODY=(vx={vx:.2f}, vy={vy:.2f}, vz={vz:.2f}) [m/s], duration={duration:.2f}s"
    )

    while time.time() - t0 < duration:
        if not _is_guided(master):
            print("[M2] mode is no longer GUIDED -> STOP")
            return False

        try:
            master.mav.set_position_target_local_ned_send(
                0,
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_FRAME_BODY_NED,
                type_mask,
                0,
                0,
                0,
                vx,
                vy,
                vz,
                0,
                0,
                0,
                0,
                0,
            )
        except Exception as exc:
            print("[M2] Error:", exc)
            return False

        time.sleep(dt)

    return True


def _method_accel_ned(master: Any, dx: float, dy: float, dz: float) -> bool:
    if not _is_guided(master):
        return False

    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dist < 1e-3:
        print("[M3] Vector ~0, nothing to do.")
        return True

    accel = max(DEFAULT_ACCEL_MAG, 1e-3)
    duration = math.sqrt(2 * dist / accel)

    ux, uy, uz = dx / dist, dy / dist, dz / dist
    ax = accel * ux
    ay = accel * uy
    az = accel * uz

    type_mask = 3135
    dt = 1 / DEFAULT_ACCEL_SEND_RATE_HZ if DEFAULT_ACCEL_SEND_RATE_HZ > 0 else 0.2
    t0 = time.time()

    print(
        f"[M3] move by NED offset (dx={dx}, dy={dy}, dz={dz}) [m] "
        f"-> aNED=(ax={ax:.2f}, ay={ay:.2f}, az={az:.2f}) [m/s^2], duration~{duration:.2f}s"
    )

    while time.time() - t0 < duration:
        if not _is_guided(master):
            print("[M3] mode is no longer GUIDED -> STOP")
            return False

        try:
            master.mav.set_position_target_local_ned_send(
                0,
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_FRAME_LOCAL_NED,
                type_mask,
                0,
                0,
                0,
                0,
                0,
                0,
                ax,
                ay,
                az,
                0,
                0,
            )
        except Exception as exc:
            print("[M3] Error:", exc)
            return False

        time.sleep(dt)

    return True


def send_vector_command(
    *,
    vector: tuple[float, float, float],
    device: str = "/dev/ttyAMA0",
    baud: int = 57600,
    method_id: int = 0,
) -> bool:
    dx, dy, dz = vector

    try:
        master = _connect(device, baud)
    except Exception as exc:
        print("[dispatcher] Connection error:", exc)
        return False

    if method_id == 0:
        return _method_position_offset(master, dx, dy, dz)
    if method_id == 1:
        return _method_velocity_ned(master, dx, dy, dz)
    if method_id == 2:
        return _method_velocity_body(master, dx, dy, dz)
    if method_id == 3:
        return _method_accel_ned(master, dx, dy, dz)

    print(f"[dispatcher] Invalid method_id: {method_id} (expected 0..3)")
    return False
