import datetime
import threading
import time
from typing import Any

try:
    from pymavlink import mavutil  # type: ignore
except Exception as exc:  # pragma: no cover - depends on runtime image
    mavutil = None
    _MAV_IMPORT_ERROR = exc
else:
    _MAV_IMPORT_ERROR = None

_master = None
_current_device: str | None = None
_current_baud: int | None = None
_state_lock = threading.Lock()
_state: dict[str, Any] = {}
_receiver_thread: threading.Thread | None = None
_receiver_stop_event = threading.Event()


def _request_data_streams(master: Any, position_hz: int = 5, extra1_hz: int = 10, ext_status_hz: int = 2) -> None:
    try:
        target_system = master.target_system
        target_component = master.target_component

        master.mav.request_data_stream_send(
            target_system,
            target_component,
            mavutil.mavlink.MAV_DATA_STREAM_POSITION,
            position_hz,
            1,
        )
        master.mav.request_data_stream_send(
            target_system,
            target_component,
            mavutil.mavlink.MAV_DATA_STREAM_EXTRA1,
            extra1_hz,
            1,
        )
        master.mav.request_data_stream_send(
            target_system,
            target_component,
            mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS,
            ext_status_hz,
            1,
        )

        print(
            f"[MAV] Requested data streams: POSITION={position_hz}Hz, "
            f"EXTRA1={extra1_hz}Hz, EXT_STATUS={ext_status_hz}Hz"
        )
    except Exception as exc:
        print("[MAV] request_data_stream_send failed:", exc)


def _ensure_connection(device: str, baud: int, heartbeat_timeout: float = 5.0) -> None:
    global _master, _current_device, _current_baud, _receiver_thread

    if mavutil is None:
        raise RuntimeError(f"pymavlink unavailable: {_MAV_IMPORT_ERROR}")

    if (
        _master is not None
        and _current_device == device
        and _current_baud == baud
        and _receiver_thread is not None
        and _receiver_thread.is_alive()
    ):
        return

    if _master is not None:
        try:
            _master.close()
        except Exception:
            pass
        _master = None

    _receiver_stop_event.clear()
    _state.clear()

    print(f"[MAV] Opening MAVLink connection on {device} @ {baud}...")
    _master = mavutil.mavlink_connection(device, baud=baud)
    _current_device = device
    _current_baud = baud

    hb = _master.wait_heartbeat(timeout=heartbeat_timeout)
    if hb is None:
        raise TimeoutError(f"[MAV] No HEARTBEAT on {device} within {heartbeat_timeout} s")

    print(
        f"[MAV] Got HEARTBEAT: system={_master.target_system}, "
        f"component={_master.target_component}, type={hb.type}, autopilot={hb.autopilot}"
    )

    _request_data_streams(_master)

    if _receiver_thread is None or not _receiver_thread.is_alive():
        _receiver_thread = threading.Thread(target=_receiver_worker, daemon=True)
        _receiver_thread.start()
        print("[MAV] Receiver thread started.")


def _receiver_worker() -> None:
    global _master
    print("[MAV] _receiver_worker: started.")

    while not _receiver_stop_event.is_set():
        if _master is None:
            time.sleep(0.1)
            continue

        try:
            msg = _master.recv_match(blocking=True, timeout=1.0)
        except Exception:
            msg = None

        if msg is None:
            continue

        mtype = msg.get_type()
        with _state_lock:
            _state[mtype] = msg


def _build_telemetry_json() -> dict[str, Any] | None:
    with _state_lock:
        gp = _state.get("GLOBAL_POSITION_INT")
        att = _state.get("ATTITUDE")
        sys = _state.get("SYS_STATUS")
        hb = _state.get("HEARTBEAT")

    if gp is None and att is None and sys is None and hb is None:
        return None

    ts = datetime.datetime.utcnow().isoformat() + "Z"

    if gp is not None:
        lat = gp.lat / 1e7
        lon = gp.lon / 1e7
        alt = gp.relative_alt / 1000.0
        vx = gp.vx / 100.0
        vy = gp.vy / 100.0
        vz = gp.vz / 100.0
    else:
        lat = lon = alt = None
        vx = vy = vz = None

    if att is not None:
        roll = att.roll
        pitch = att.pitch
        yaw = att.yaw
    else:
        roll = pitch = yaw = None

    if sys is not None:
        voltage = sys.voltage_battery / 1000.0 if sys.voltage_battery != 65535 else None
        percent = sys.battery_remaining if sys.battery_remaining != 255 else None
    else:
        voltage = percent = None

    mode = None
    armed = None
    if hb is not None:
        try:
            mode = mavutil.mode_string_v10(hb)
        except Exception:
            mode = None
        try:
            armed = bool(hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        except Exception:
            armed = None

    return {
        "ts": ts,
        "position": {"lat": lat, "lon": lon, "alt": alt},
        "velocity": {"vx": vx, "vy": vy, "vz": vz},
        "attitude": {"yaw": yaw, "pitch": pitch, "roll": roll},
        "battery": {"voltage": voltage, "percent": percent},
        "mode": mode,
        "armed": armed,
        "extras": {},
    }


def get_telemetry_json(
    device: str,
    baud: int,
    wait_for_data: bool = True,
    timeout: float = 2.0,
) -> dict[str, Any] | None:
    _ensure_connection(device, baud)

    start = time.time()
    while True:
        telemetry = _build_telemetry_json()
        if telemetry is not None:
            return telemetry

        if not wait_for_data:
            return None

        if time.time() - start > timeout:
            return None

        time.sleep(0.05)
