"""
Manual diagnostic: commands the drone 1m forward/back/left/right (net-zero)
and checks the ACTUAL displacement (read from MAVLink LOCAL_POSITION_NED)
against what was commanded, using the same move-dispatch code the mission
pipeline uses (drone_control.actuators.pixhawk_vector_backend).

This talks to a REAL flight controller (or SITL) over MAVLink and WILL MOVE
THE VEHICLE if run with --execute. It is intentionally NOT a pytest test:
nothing in here runs automatically as part of the test suite.

Usage:
    # status/dry-run only, sends nothing:
    python3 -m scripts.check_movement_accuracy --device /dev/ttyAMA0

    # actually move the vehicle 1m per leg:
    python3 -m scripts.check_movement_accuracy --device /dev/ttyAMA0 --execute

    # against SITL instead of real hardware:
    python3 -m scripts.check_movement_accuracy --device udp:127.0.0.1:14550 --execute

Safety:
    - Defaults to a dry run (prints what it would send, sends nothing).
    - Pass --execute to actually send commands.
    - Refuses to execute unless the vehicle reports GUIDED mode and ARMED.
    - Prompts for interactive y/N confirmation before every single leg.
    - Legs are North/South/East/West of equal magnitude, so a fully
      successful run returns the vehicle to its start point.
"""

import argparse
import math
import sys
import time
from dataclasses import dataclass

try:
    from pymavlink import mavutil  # type: ignore
except Exception as exc:  # pragma: no cover - depends on runtime image
    mavutil = None
    _MAV_IMPORT_ERROR = exc
else:
    _MAV_IMPORT_ERROR = None

from drone_control.actuators.pixhawk_vector_backend import send_vector_command_via
from drone_control.utils.coords import grid_xyz_to_ned


@dataclass
class Leg:
    name: str
    move_xyz: tuple[float, float, float]  # grid convention: x=East, y=North, z=Up


def build_legs(distance: float) -> list[Leg]:
    return [
        Leg("forward (North)", (0.0, distance, 0.0)),
        Leg("backward (South)", (0.0, -distance, 0.0)),
        Leg("right (East)", (distance, 0.0, 0.0)),
        Leg("left (West)", (-distance, 0.0, 0.0)),
    ]


def connect(device: str, baud: int, heartbeat_timeout: float):
    if mavutil is None:
        raise RuntimeError(f"pymavlink unavailable: {_MAV_IMPORT_ERROR}")
    print(f"[connect] Opening MAVLink connection on {device} @ {baud}...")
    master = mavutil.mavlink_connection(device, baud=baud)
    hb = master.wait_heartbeat(timeout=heartbeat_timeout)
    if hb is None:
        raise TimeoutError(f"No HEARTBEAT on {device} within {heartbeat_timeout}s")
    print(
        f"[connect] Got heartbeat: system={master.target_system} component={master.target_component}"
    )

    master.mav.request_data_stream_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_DATA_STREAM_POSITION,
        10,
        1,
    )
    return master


def get_mode_and_armed(master) -> tuple[str, bool]:
    hb = master.recv_match(
        type="HEARTBEAT", blocking=True, timeout=3.0
    ) or master.messages.get("HEARTBEAT")
    if hb is None:
        return "UNKNOWN", False
    try:
        mode = mavutil.mode_string_v10(hb)
    except Exception:
        mode = "UNKNOWN"
    armed = bool(hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
    return mode, armed


def read_local_position(master, timeout: float) -> tuple[float, float, float] | None:
    """Drain incoming messages and return the freshest LOCAL_POSITION_NED (n, e, d) in meters."""
    deadline = time.time() + timeout
    latest = None
    while time.time() < deadline:
        msg = master.recv_match(type="LOCAL_POSITION_NED", blocking=True, timeout=0.5)
        if msg is not None:
            latest = (msg.x, msg.y, msg.z)
    return latest


def wait_for_settle(
    master, poll_interval: float, max_wait: float, still_threshold: float = 0.05
):
    """Poll LOCAL_POSITION_NED until it stops changing (or max_wait elapses)."""
    deadline = time.time() + max_wait
    last = read_local_position(master, timeout=poll_interval)
    while time.time() < deadline:
        current = read_local_position(master, timeout=poll_interval)
        if (
            last is not None
            and current is not None
            and math.dist(last, current) < still_threshold
        ):
            return current
        if current is not None:
            last = current
    return last


def confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")


def run(args) -> int:
    try:
        master = connect(
            args.device, args.baud, heartbeat_timeout=args.heartbeat_timeout
        )
    except Exception as exc:
        print(f"[abort] Could not connect to {args.device}: {exc}")
        return 1

    mode, armed = get_mode_and_armed(master)
    print(f"[status] mode={mode} armed={armed}")

    if args.execute:
        if mode != "GUIDED":
            print("[abort] Vehicle is not in GUIDED mode. Switch to GUIDED and re-run.")
            return 1
        if not armed:
            print("[abort] Vehicle is not armed. Arm it and re-run.")
            return 1
        print(
            f"\n*** This will command the REAL vehicle to move {args.distance}m in each of "
            "forward/backward/right/left. ***"
        )
        print(
            "*** Make sure you have clear airspace and are ready to take manual control. ***"
        )
        if not confirm("Proceed?"):
            print("[abort] User declined.")
            return 1
    else:
        print(
            "[dry-run] --execute not passed: legs will be listed but nothing will be sent."
        )

    results = []
    for leg in build_legs(args.distance):
        expected_ned = grid_xyz_to_ned(leg.move_xyz)
        print(
            f"\n--- Leg: {leg.name} (grid xyz={leg.move_xyz}, NED={expected_ned}) ---"
        )

        if not args.execute:
            continue
        if not confirm("Send this leg now?"):
            print("[skip] User skipped this leg.")
            continue

        pos_before = read_local_position(master, timeout=args.heartbeat_timeout)
        if pos_before is None:
            print(
                "[error] Could not read LOCAL_POSITION_NED before move; aborting remaining legs."
            )
            break

        ok = send_vector_command_via(master, vector=expected_ned, method_id=args.method)
        if not ok:
            print("[error] Move dispatch reported failure.")

        pos_after = wait_for_settle(
            master, poll_interval=1.0, max_wait=args.settle_timeout
        )
        if pos_after is None:
            print("[error] Could not read LOCAL_POSITION_NED after move.")
            continue

        actual = tuple(a - b for a, b in zip(pos_after, pos_before))
        error = tuple(a - e for a, e in zip(actual, expected_ned))
        error_mag = math.sqrt(sum(e * e for e in error))
        passed = error_mag <= args.tolerance

        print(f"Actual NED displacement: {tuple(round(v, 3) for v in actual)}")
        print(
            f"Error vector: {tuple(round(v, 3) for v in error)} "
            f"(magnitude={error_mag:.3f}m, tolerance={args.tolerance}m)"
        )
        print("RESULT: " + ("PASS" if passed else "FAIL"))

        results.append((leg.name, expected_ned, actual, error_mag, passed))

    if results:
        print("\n=== Summary ===")
        for name, expected, actual, error_mag, passed in results:
            status = "PASS" if passed else "FAIL"
            print(
                f"{status:4s} {name:18s} expected={tuple(round(v, 2) for v in expected)} "
                f"actual={tuple(round(v, 2) for v in actual)} error={error_mag:.3f}m"
            )
        n_pass = sum(1 for r in results if r[-1])
        print(f"\n{n_pass}/{len(results)} legs within tolerance.")
        return 0 if n_pass == len(results) else 2

    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--device",
        default="/dev/ttyAMA0",
        help="MAVLink device, e.g. /dev/ttyAMA0 or udp:127.0.0.1:14550 for SITL",
    )
    p.add_argument("--baud", type=int, default=57600)
    p.add_argument("--distance", type=float, default=1.0, help="Meters to move per leg")
    p.add_argument(
        "--method",
        type=int,
        default=0,
        choices=[0, 1, 2, 3],
        help="Movement method id, same meaning as MOVE_METHOD in pixhawk_vector_backend.py",
    )
    p.add_argument(
        "--tolerance",
        type=float,
        default=0.3,
        help="Max allowed error magnitude in meters",
    )
    p.add_argument(
        "--settle-timeout",
        type=float,
        default=15.0,
        help="Max seconds to wait for position to stop changing after a move",
    )
    p.add_argument("--heartbeat-timeout", type=float, default=10.0)
    p.add_argument(
        "--execute",
        action="store_true",
        help="Actually send move commands. Without this flag the script only connects, "
        "reports vehicle status, and lists what it WOULD send.",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n[abort] Interrupted by user.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
