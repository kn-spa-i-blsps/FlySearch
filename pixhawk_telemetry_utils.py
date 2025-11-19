#!/usr/bin/env python3
"""
Narzędzia do zbierania telemetrii z Pixhawka i zapisu w formacie JSON.

Struktura pojedynczego rekordu:

{
  "ts": null,
  "position": { "lat": null, "lon": null, "alt": null },
  "velocity": { "vx": null, "vy": null, "vz": null },
  "attitude": { "yaw": null, "pitch": null, "roll": null },
  "battery":  { "voltage": null, "percent": null },
  "mode": null,
  "armed": null,
  "extras": {}
}
"""

import json
import os
import time
import datetime
import threading
from typing import Optional, Dict, Any

from pymavlink import mavutil

# ---------------------------------------------------------------------------
# Zmiennie globalne związane z połączeniem MAVLink
# ---------------------------------------------------------------------------

_master: Optional[mavutil.mavfile] = None
_current_device: Optional[str] = None
_current_baud: Optional[int] = None

_state_lock = threading.Lock()
_state: Dict[str, Any] = {}  # ostatnie wiadomości wg typu (np. "GLOBAL_POSITION_INT")

_receiver_thread: Optional[threading.Thread] = None
_receiver_stop_event = threading.Event()

# ---------------------------------------------------------------------------
# Zmiennie globalne związane z ciągłym logowaniem (funkcje 3 i 4)
# ---------------------------------------------------------------------------

_logging_thread: Optional[threading.Thread] = None
_logging_stop_event: Optional[threading.Event] = None
_current_log_file_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Funkcje pomocnicze: połączenie i odbiór wiadomości MAVLink
# ---------------------------------------------------------------------------

def _ensure_connection(device: str, baud: int, heartbeat_timeout: float = 5.0) -> None:
    """
    Zapewnia połączenie MAVLink z danym urządzeniem i uruchomiony wątek odbioru.
    Jeśli połączenie już istnieje z tym samym device/baud, nic nie robi.
    """
    global _master, _current_device, _current_baud, _receiver_thread

    if (
        _master is not None
        and _current_device == device
        and _current_baud == baud
        and _receiver_thread is not None
        and _receiver_thread.is_alive()
    ):
        # Połączenie już istnieje i działa.
        return

    # Jeśli jest stare połączenie – zakończ je
    if _master is not None:
        try:
            _master.close()
        except Exception:
            pass
        _master = None

    _receiver_stop_event.clear()
    _state.clear()

    # Nawiąż nowe połączenie
    _master = mavutil.mavlink_connection(device, baud=baud)
    _current_device = device
    _current_baud = baud

    # Czekamy na HEARTBEAT, żeby upewnić się, że autopilot odpowiada
    _master.wait_heartbeat(timeout=heartbeat_timeout)

    # Startujemy wątek odbioru, jeśli jeszcze nie działa
    if _receiver_thread is None or not _receiver_thread.is_alive():
        _receiver_thread = threading.Thread(target=_receiver_worker, daemon=True)
        _receiver_thread.start()


def _receiver_worker() -> None:
    """
    Wątek, który w tle odbiera wiadomości MAVLink i aktualizuje _state.
    """
    global _master

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


def _build_telemetry_json() -> Optional[Dict[str, Any]]:
    """
    Buduje słownik telemetryjny z ostatnich wiadomości MAVLink w _state.
    Zwraca dict w zadanym formacie albo None, jeśli nie ma jeszcze sensownych danych.
    """
    with _state_lock:
        gp = _state.get("GLOBAL_POSITION_INT")
        att = _state.get("ATTITUDE")
        sys = _state.get("SYS_STATUS")
        hb = _state.get("HEARTBEAT")

    if gp is None and att is None and sys is None and hb is None:
        return None

    # znacznik czasu UTC
    ts = datetime.datetime.utcnow().isoformat() + "Z"

    # position + velocity
    if gp is not None:
        lat = gp.lat / 1e7
        lon = gp.lon / 1e7
        alt = gp.relative_alt / 1000.0  # m nad HOME
        vx = gp.vx / 100.0  # cm/s -> m/s
        vy = gp.vy / 100.0
        vz = gp.vz / 100.0
    else:
        lat = lon = alt = None
        vx = vy = vz = None

    # attitude
    if att is not None:
        roll = att.roll   # rad
        pitch = att.pitch
        yaw = att.yaw
    else:
        roll = pitch = yaw = None

    # battery
    if sys is not None:
        voltage = sys.voltage_battery / 1000.0 if sys.voltage_battery != 65535 else None
        percent = sys.battery_remaining if sys.battery_remaining != 255 else None
    else:
        voltage = percent = None

    # mode & armed
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

    telemetry = {
        "ts": ts,
        "position": {"lat": lat, "lon": lon, "alt": alt},
        "velocity": {"vx": vx, "vy": vy, "vz": vz},
        "attitude": {"yaw": yaw, "pitch": pitch, "roll": roll},
        "battery": {"voltage": voltage, "percent": percent},
        "mode": mode,
        "armed": armed,
        "extras": {},  # zostawiamy pusty słownik na przyszłe rozszerzenia
    }

    return telemetry


# ---------------------------------------------------------------------------
# 1) Funkcja: pobranie pojedynczego JSON-a z telemetrii
# ---------------------------------------------------------------------------

def get_telemetry_json(
    device: str,
    baud: int,
    wait_for_data: bool = True,
    timeout: float = 2.0,
) -> Optional[Dict[str, Any]]:
    """
    Funkcja 1:
    Zbiera z Pixhawka dane telemetryczne potrzebne do zbudowania JSON-a i zwraca
    ten JSON jako słownik (lub None, jeśli nie udało się nic sensownego zebrać).

    Parametry:
      - device: np. "/dev/tty.usbserial-D30JQ57H"
      - baud: np. 57600 albo 115200
      - wait_for_data: jeśli True, czekamy maksymalnie 'timeout' sekund na dane
      - timeout: ile maksymalnie czekać na pojawienie się danych

    Zwraca:
      dict w zadanym formacie albo None
    """
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


# ---------------------------------------------------------------------------
# 2) Funkcja: zapis pojedynczego JSON-a do pliku (nadpisuje)
# ---------------------------------------------------------------------------

def save_single_snapshot_to_file(
    file_path: str,
    device: str,
    baud: int,
) -> Optional[Dict[str, Any]]:
    """
    Funkcja 2:
    Na podstawie funkcji 1 pobiera pojedynczy JSON z telemetrii i zapisuje go
    do pliku o podanej ścieżce, NADPISUJĄC poprzednią zawartość.

    Parametry:
      - file_path: pełna ścieżka do pliku (np. "/tmp/telemetry.json")
      - device: port MAVLink (np. "/dev/tty.usbserial-D30JQ57H")
      - baud: prędkość (np. 57600)

    Zwraca:
      dict (ten sam zapisany JSON) albo None (gdy brak danych)
    """
    telemetry = get_telemetry_json(device, baud)
    if telemetry is None:
        return None

    # Upewniamy się, że katalog istnieje
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, "w") as f:
        json.dump(telemetry, f, indent=2)

    return telemetry


# ---------------------------------------------------------------------------
# 3) Funkcja: start ciągłego logowania co 0.2 s do nowego pliku
# ---------------------------------------------------------------------------

def start_continuous_logging(
    device: str,
    baud: int,
    output_dir: str,
    interval: float = 0.2,
    filename_prefix: str = "telemetry",
    file_extension: str = ".txt",
) -> str:
    """
    Funkcja 3:
    Rozpoczyna ciągłe logowanie – co 'interval' sekund tworzy nowego JSON-a
    (funkcja 1) i dopisuje go jako jedną linię do nowo stworzonego pliku.

    Nazwa pliku jest tworzona na podstawie czasu rozpoczęcia logowania, np.:
      telemetry_2025-11-19_20-31-10.txt

    Parametry:
      - device, baud: parametry połączenia z Pixhawkiem
      - output_dir: katalog, w którym ma się pojawić plik logu
      - interval: odstęp między próbkami (domyślnie 0.2 s)
      - filename_prefix: prefiks nazwy pliku
      - file_extension: rozszerzenie pliku (np. ".txt" lub ".log")

    Zwraca:
      pełną ścieżkę do utworzonego pliku logu
    """
    global _logging_thread, _logging_stop_event, _current_log_file_path

    if _logging_thread is not None and _logging_thread.is_alive():
        raise RuntimeError("Logowanie już jest uruchomione. Najpierw wywołaj stop_continuous_logging().")

    # Upewniamy się, że połączenie istnieje
    _ensure_connection(device, baud)

    # Przygotowujemy nazwę pliku
    os.makedirs(output_dir, exist_ok=True)
    start_ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{filename_prefix}_{start_ts}{file_extension}"
    log_path = os.path.join(output_dir, filename)

    _current_log_file_path = log_path
    _logging_stop_event = threading.Event()

    def _logging_worker():
        while not _logging_stop_event.is_set():
            telemetry = get_telemetry_json(device, baud, wait_for_data=False)
            if telemetry is not None:
                try:
                    with open(log_path, "a") as f:
                        json.dump(telemetry, f)
                        f.write("\n")
                except Exception as e:
                    print("Błąd przy zapisie do pliku logu:", e)
            time.sleep(interval)

    _logging_thread = threading.Thread(target=_logging_worker, daemon=True)
    _logging_thread.start()

    return log_path


# ---------------------------------------------------------------------------
# 4) Funkcja: zakończenie logowania z funkcji 3
# ---------------------------------------------------------------------------

def stop_continuous_logging(
    device: str,
    baud: int,
) -> Optional[str]:
    """
    Funkcja 4:
    Kończy logowanie rozpoczęte w start_continuous_logging().

    device i baud są tu parametrami tylko dla spójności interfejsu
    (nie są bezpośrednio używane).

    Zwraca:
      ścieżkę do pliku, do którego logowano, albo None jeśli logowanie nie było aktywne.
    """
    global _logging_thread, _logging_stop_event, _current_log_file_path

    _ = device
    _ = baud

    if _logging_thread is None or not _logging_thread.is_alive():
        return None

    if _logging_stop_event is not None:
        _logging_stop_event.set()

    _logging_thread.join(timeout=5.0)
    _logging_thread = None

    return _current_log_file_path


# ---------------------------------------------------------------------------
# (Opcjonalne) Przykładowe użycie z linii poleceń
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Przykład: podmień na swój port:
    device = "/dev/tty.usbserial-D30JQ57H"
    baud = 57600

    print("Pobieram pojedynczy snapshot...")
    snap = get_telemetry_json(device, baud)
    print("Snapshot:", snap)

    if snap:
        path_single = "/tmp/pixhawk_single_snapshot.json"
        save_single_snapshot_to_file(path_single, device, baud)
        print("Zapisano pojedynczy snapshot do:", path_single)

        print("Start logowania ciągłego na 5 sekund...")
        log_path = start_continuous_logging(device, baud, output_dir="/tmp")
        print("Logowanie do pliku:", log_path)
        time.sleep(5.0)
        stop_continuous_logging(device, baud)
        print("Zakończono logowanie.")
