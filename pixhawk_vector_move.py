#!/usr/bin/env python3
"""
pixhawk_vector_move.py

Drone control in ArduPilot (GUIDED) using a displacement vector (dx, dy, dz)
with four methods, selected by method_id 0–3.

method_id:
    0 -> POSITION OFFSET (LOCAL_OFFSET_NED)
    1 -> VELOCITY NED (LOCAL_NED, duration and velocity computed from the vector)
    2 -> VELOCITY BODY (BODY_NED, duration and velocity computed from the vector)
    3 -> ACCELERATION NED (LOCAL_NED, duration and acceleration computed from the vector)

Input:
    send_vector_command(device, baud, vector, method_id)

    vector = (dx, dy, dz) [meters]
"""

import time
import math
from typing import Tuple

from pymavlink import mavutil

# Default parameters to calculate duration etc.
DEFAULT_SPEED = 1.0               # [m/s] for methods 1 and 2
DEFAULT_VEL_SEND_RATE_HZ = 5.0    # [Hz]
DEFAULT_ACCEL_MAG = 0.5           # [m/s^2] for methods 3
DEFAULT_ACCEL_SEND_RATE_HZ = 5.0  # [Hz]


# ---------------------------------- UTILS ----------------------------------


def _connect(device: str, baud: int, heartbeat_timeout: float = 5.0) -> mavutil.mavfile:
    master = mavutil.mavlink_connection(device, baud=baud)
    master.wait_heartbeat(timeout=heartbeat_timeout)
    return master


def _get_mode(master: mavutil.mavfile) -> str:
    hb = master.recv_match(type="HEARTBEAT", blocking=True, timeout=1.0)
    if hb is None:
        hb = master.messages.get("HEARTBEAT")
        if hb is None:
            return "UNKNOWN"
    try:
        return mavutil.mode_string_v10(hb)
    except Exception:
        return "UNKNOWN"


def _is_guided(master: mavutil.mavfile) -> bool:
    mode = _get_mode(master)
    print(f"[vector_move] Current mode: {mode}")
    return mode == "GUIDED"


# ---------------------------------- METHOD 0 ----------------------------------
# No changes – shift by a vector in LOCAL_OFFSET_NED


def method_position_offset(master, dx, dy, dz) -> bool:
   # if not _is_guided(master):
   #     print("[M0] Drone not in GUIDED – interrupted.")
   #     return False

    type_mask = 0b0000111111111000  # use position
    try:
        master.mav.set_position_target_local_ned_send(
            0,
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_FRAME_LOCAL_OFFSET_NED,
            type_mask,
            dx, dy, dz,
            0, 0, 0,
            0, 0, 0,
            0, 0
        )
        print(f"[M0] Sent offset (dx={dx}, dy={dy}, dz={dz}) [m, NED]")
        return True
    except Exception as e:
        print("[M0] Error:", e)
        return False


# ---------------------------------- METHOD 1 ----------------------------------
# VELOCITY NED – calculating vx,vy,vz and duration from vecotr (dx,dy,dz)


def method_velocity_ned(master, dx, dy, dz) -> bool:
    #if not _is_guided(master):
     #   return False

    dist = math.sqrt(dx*dx + dy*dy + dz*dz)
    if dist < 1e-3:
        print("[M1] Vector ~0, doing nothing.")
        return True

    speed = max(DEFAULT_SPEED, 0.01)
    duration = dist / speed

    vx = dx / duration
    vy = dy / duration
    vz = dz / duration

    type_mask = 3527  # use velocity
    dt = 1 / DEFAULT_VEL_SEND_RATE_HZ if DEFAULT_VEL_SEND_RATE_HZ > 0 else 0.2
    t0 = time.time()

    print(f"[M1] move by NED offset (dx={dx}, dy={dy}, dz={dz}) [m] "
          f"→ vNED=(vx={vx:.2f}, vy={vy:.2f}, vz={vz:.2f}) [m/s], "
          f"duration={duration:.2f}s")

    while time.time() - t0 < duration:

        #if not _is_guided(master):
       #     print("[M1] tryb przestał być GUIDED → STOP")
      #      return False

        try:
            master.mav.set_position_target_local_ned_send(
                0,
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_FRAME_LOCAL_NED,
                type_mask,
                0, 0, 0,
                vx, vy, vz,
                0, 0, 0,
                0, 0
            )
        except Exception as e:
            print("[M1] Error:", e)
            return False

        time.sleep(dt)

    return True


# ---------------------------------- METHOD 2 ----------------------------------
# VELOCITY BODY – same as M1, but in the drone's axis (BODY_NED)


def method_velocity_body(master, dx, dy, dz) -> bool:
    #if not _is_guided(master):
    #    return False

    dist = math.sqrt(dx*dx + dy*dy + dz*dz)
    if dist < 1e-3:
        print("[M2] Vector ~0, doing nothing.")
        return True

    speed = max(DEFAULT_SPEED, 0.01)
    duration = dist / speed

    vx = dx / duration
    vy = dy / duration
    vz = dz / duration

    type_mask = 3527  # use velocity
    dt = 1 / DEFAULT_VEL_SEND_RATE_HZ if DEFAULT_VEL_SEND_RATE_HZ > 0 else 0.2
    t0 = time.time()

    print(f"[M2] move by BODY offset (dx={dx}, dy={dy}, dz={dz}) [m in drone's axis] "
          f"→ vBODY=(vx={vx:.2f}, vy={vy:.2f}, vz={vz:.2f}) [m/s], "
          f"duration={duration:.2f}s")

    while time.time() - t0 < duration:

        if not _is_guided(master):
            print("[M2] mode is no longer GUIDED → STOP")
            return False

        try:
            master.mav.set_position_target_local_ned_send(
                0,
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_FRAME_BODY_NED,
                type_mask,
                0, 0, 0,
                vx, vy, vz,
                0, 0, 0,
                0, 0
            )
        except Exception as e:
            print("[M2] Error:", e)
            return False

        time.sleep(dt)

    return True


# ---------------------------------- METHOD 3 ----------------------------------
# ACCEL NED – calculating a constant value of acceletation in the direction of the vector 


def method_accel_ned(master, dx, dy, dz) -> bool:
    if not _is_guided(master):
        return False

    dist = math.sqrt(dx*dx + dy*dy + dz*dz)
    if dist < 1e-3:
        print("[M3] Vector ~0, doing nothing.")
        return True

    a = max(DEFAULT_ACCEL_MAG, 1e-3)

    # motion with acceletation a: s = 1/2 * a * t^2  → t = sqrt(2 s / a)
    duration = math.sqrt(2 * dist / a)

    ux, uy, uz = dx/dist, dy/dist, dz/dist
    ax = a * ux
    ay = a * uy
    az = a * uz

    type_mask = 3135  # use acceleration
    dt = 1 / DEFAULT_ACCEL_SEND_RATE_HZ if DEFAULT_ACCEL_SEND_RATE_HZ > 0 else 0.2
    t0 = time.time()

    print(f"[M3] move by NED offset (dx={dx}, dy={dy}, dz={dz}) [m] "
          f"→ aNED=(ax={ax:.2f}, ay={ay:.2f}, az={az:.2f}) [m/s^2], "
          f"duration~{duration:.2f}s")

    while time.time() - t0 < duration:

        if not _is_guided(master):
            print("[M3] mode is no longer GUIDED → STOP")
            return False

        try:
            master.mav.set_position_target_local_ned_send(
                0,
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_FRAME_LOCAL_NED,
                type_mask,
                0, 0, 0,
                0, 0, 0,
                ax, ay, az,
                0, 0
            )
        except Exception as e:
            print("[M3] Error:", e)
            return False

        time.sleep(dt)

    return True


# ---------------------------------- DISPATCHER ----------------------------------


def send_vector_command(
    vector: Tuple[float, float, float],
    device: str = '/dev/ttyAMA0',
    baud: int = 57600,
    method_id: int = 0,
) -> bool:
    """
    Main function: You provide ONLY:
        - device (e.g. "/dev/tty.usbserial-D30JQ57H")
        - baud (e.g. 57600)
        - vector = (dx, dy, dz) [meters]
        - method_id 0–3

    Depending on method_id, we compute internally:
        - velocity (methods 1,2) and duration,
        - acceleration (method 3) and duration,
        - or we send an offset (method 0).

    method_id:
        0 = POSITION_OFFSET   (LOCAL_OFFSET_NED)
        1 = VELOCITY_NED      (LOCAL_NED, displacement -> velocity+time)
        2 = VELOCITY_BODY     (BODY_NED, displacement -> velocity+time)
        3 = ACCEL_NED         (LOCAL_NED, displacement -> accel+time)
    """
    dx, dy, dz = vector

    try:
        master = _connect(device, baud)
    except Exception as e:
        print("[dispatcher] Connection error:", e)
        return False

    if method_id == 0:
        return method_position_offset(master, dx, dy, dz)

    elif method_id == 1:
        return method_velocity_ned(master, dx, dy, dz)

    elif method_id == 2:
        return method_velocity_body(master, dx, dy, dz)

    elif method_id == 3:
        return method_accel_ned(master, dx, dy, dz)

    else:
        print(f"[dispatcher] Incorrect method_id: {method_id} (0–3)")
        return False


# ---------------------------------- TEST MAIN ----------------------------------

if __name__ == "__main__":

    device = "/dev/tty.usbserial-D30JQ57H"
    baud = 57600

    print("\n=== TEST: method 0 offset (2m North, 0.5m up) ===")
    send_vector_command(device, baud, (2.0, 0.0, -0.5), method_id=0)

    print("\n=== TEST: method 1 velocity_ned (the same vector) ===")
    send_vector_command(device, baud, (2.0, 0.0, -0.5), method_id=1)

    print("\n=== TEST: method 2 velocity_body (the same vector with respect to the nose) ===")
    send_vector_command(device, baud, (2.0, 0.0, -0.5), method_id=2)

    print("\n=== TEST: method 3 accel_ned (the same vector) ===")
    send_vector_command(device, baud, (2.0, 0.0, -0.5), method_id=3)