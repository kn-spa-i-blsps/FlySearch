import argparse
import base64
import json
import os
import pathlib
import uuid
from datetime import datetime
from capture import capture_bytes

import websocket
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import PyavOutput

try:
    from pixhawk_telemetry_utils import get_telemetry_json
except Exception as e:
    get_telemetry_json = None
    print(f"[RPi] WARN: pixhawk_telemetry not available: {e} – will send empty telemetry.")

try:
    from pixhawk_vector_move import send_vector_command
except Exception as e:
    send_vector_command = None
    print(f"[RPi] WARN: pixhawk_vector_move not available: {e} – will NOT execute moves.")

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--server",  default=os.environ.get("SERVER_URL", "ws://127.0.0.1:8080"))
    p.add_argument("--capture", default=os.environ.get("CAPTURE_PY", "/app/capture.py"))
    p.add_argument("--width",   default=int(os.environ.get("WIDTH", 500)), type=int)
    p.add_argument("--height",  default=int(os.environ.get("HEIGHT", 500)), type=int)
    p.add_argument("--quality", default=int(os.environ.get("QUALITY", 90)), type=int)
    p.add_argument("--img", default=os.environ.get("IMG_DIR", "/img"))
    p.add_argument("--commands", default=os.environ.get("COMMANDS_DIR", "/commands"))
    p.add_argument("--mav_device",  default=os.environ.get("MAV_DEVICE", "/dev/ttyAMA0"))
    p.add_argument("--mav_baud",    default=int(os.environ.get("MAV_BAUD", "57600")), type=int)
    p.add_argument("--telemetry_timeout", default=float(os.environ.get("TELEM_TIMEOUT", "2.0")), type=float)
    p.add_argument("--move_method", default=int(os.environ.get("MOVE_METHOD", "0")),   type=int)  # 0..3
    p.add_argument("--exec_moves",  default=int(os.environ.get("EXECUTE_MOVES", "1")), type=int) # 0=OFF, 1=ON
    return p.parse_args()

def main():
    args = parse_args()

    img_dir = pathlib.Path(args.img); img_dir.mkdir(parents=True, exist_ok=True)
    commands_dir = pathlib.Path(args.commands); commands_dir.mkdir(parents=True, exist_ok=True)

    shortid = uuid.uuid4().hex[:8]
    session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{shortid}"
    session_file = commands_dir / f"session_{session_id}.jsonl"
    latest_file  = commands_dir / "latest_command.json"

    encoder = H264Encoder(bitrate=10000000)
    output = PyavOutput(str(img_dir / f"video_{session_id}.mp4"))
    seq = {"n": 0}
    def next_seq():
        seq["n"] += 1
        return seq["n"]

    def now_ts():
        return datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    def append_jsonl(path: pathlib.Path, obj: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def write_json(path: pathlib.Path, obj: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

    print("[RPi] Initializing PiCamera...")
    picam2 = Picamera2()

    config = picam2.create_video_configuration(
        main={"size": (int(args.width), int(args.height)), "format": "YUV420"},
        lores={"size": (int(args.width), int(args.height)), "format": "YUV420"}
    )
    picam2.configure(config)
    picam2.start()

    recording_started = False

    try:
        print(f"[RPi] Starting video recording: {output}")
        picam2.start_recording(encoder, output)
        recording_started = True

        def take_photo_bytes():
            return capture_bytes(width=args.width, height=args.height, quality=args.quality)

        def gather_telemetry() -> dict:
            """
            Jeden snapshot z Pixhawka. Zwraca {} gdy brak modułu/połączenia.
            Dodaje pole 'height' = position.alt dla kompatybilności z serwerem.
            """
            if get_telemetry_json is None:
                return {}
            try:
                data = get_telemetry_json(
                    device=args.mav_device,
                    baud=args.mav_baud,
                    wait_for_data=True,
                    timeout=args.telemetry_timeout
                )
            except Exception as e:
                print(f"[RPi] TELEMETRY read error: {e}")
                data = None

            if not data:
                return {}

            # back-compat z parse_telemetry() po stronie serwera
            try:
                alt = (data.get("position") or {}).get("alt")
                if alt is not None and "height" not in data:
                    data["height"] = alt
            except Exception:
                pass

            return data

        def grid_xyz_to_ned(move):
            """
            Mapping from the VLM prompt: (x=E, y=N, z=UP)  →  NED: (N, E, D).
            """
            x, y, z = float(move[0]), float(move[1]), float(move[2])
            N = y
            E = x
            D = -z
            return (N, E, D)

        def maybe_execute_move(move):
            """
            If EXECUTE_MOVES=1 i and pixhawk_vector_move exists, send the command to Pixhawk.
            Returns True/False (whether the command was sent to FC).
            """
            if not args.exec_moves:
                print("[RPi] EXECUTE_MOVES=0 → just logging the command, without sending to FC.")
                return False
            if send_vector_command is None:
                print("[RPi] pixhawk_vector_move not available → command not sent to FC.")
                return False
            try:
                ned = grid_xyz_to_ned(move)
                ok = send_vector_command(
                    #device=args.mav_device,
                    #baud=args.mav_baud,
                    vector=ned,               # (N, E, D) in meters
                    #method_id=args.move_method  # 0..3
                )
                print(f"[RPi] FC execute move ned={ned} method={args.move_method} ok={ok}")
                return bool(ok)
            except Exception as e:
                print(f"[RPi] FC execute error: {e}")
                return False

        def on_message(ws, message):
            preview = message if isinstance(message, str) else f"<{len(message)} bytes>"
            print("Received:", (preview[:160] + "...") if isinstance(preview, str) and len(preview) > 160 else preview)

            if message == "SEND_PHOTO":
                photo = take_photo_bytes()
                ws.send(photo, opcode=websocket.ABNF.OPCODE_BINARY)
                print(f"Sent photo")
                return

            elif message == "TELEMETRY":
                try:
                    with open("telemetry.json", "r", encoding="utf-8") as tf:
                        tmpl = json.load(tf)
                except FileNotFoundError:
                    print("[RPi] telemetry.json not found - sending empty {}")
                    tmpl = {}
                ws.send(json.dumps({"type": "TELEMETRY", "data": tmpl}))
                print("[RPi] Sent TELEMETRY json")
                return

            elif message == "PHOTO_WITH_TELEMETRY":
                try:
                    photo = take_photo_bytes()
                    photo_base64 = base64.b64encode(photo).decode('utf-8')
                except Exception as e:
                    print(f"[RPi] PHOTO_WITH_TELEMETRY: photo error: {e}")
                    photo_base64 = None

                tel = gather_telemetry()

                payload = {
                    "type": "PHOTO_WITH_TELEMETRY",
                    "photo": photo_base64,
                    "telemetry": tel
                }
                ws.send(json.dumps(payload))
                print(f"[RPi] Sent PHOTO_WITH_TELEMETRY (photo={photo_base64 is not None}, telem_keys={list(tel.keys())})")
                return

            # --- JSON control plane: COMMAND from server ---
            obj = None
            if isinstance(message, str):
                try:
                    obj = json.loads(message)
                except Exception as e:
                    print(f"[RPi] json.loads FAILED on text message: {e}")
                    obj = None

            if isinstance(obj, dict) and obj.get("type") == "COMMAND":
                try:
                    s = next_seq()

                    # Normalizacja do lekkiego formatu na dysku
                    normalized = {
                        "ts": now_ts(),
                        "seq": s,
                    }

                    executed = False
                    if obj.get("action") == "FOUND":
                        normalized["type"] = "FOUND"
                        print("[RPi] COMMAND received: FOUND")
                        # (tu nic nie wysyłamy do FC – samo powiadomienie)
                    elif "move" in obj:
                        x, y, z = obj["move"]
                        normalized["type"] = "MOVE"
                        normalized["move"] = [float(x), float(y), float(z)]
                        print(f"[RPi] COMMAND received: MOVE (x={x}, y={y}, z={z})")
                        # próba wykonania (opcjonalnie, zależnie od EXECUTE_MOVES)
                        executed = maybe_execute_move((x, y, z))
                    else:
                        print("[RPi] Unknown COMMAND payload:", obj)
                        return

                    append_jsonl(session_file, normalized)
                    write_json(latest_file, normalized)
                    print(f"[RPi] COMMAND stored (seq={s}) → {session_file.name}; latest_command.json updated")

                    ws.send(json.dumps({
                        "type": "ACK",
                        "of": "COMMAND",
                        "ok": True,
                        "seq": s,
                        "executed": executed
                    }))
                    print(f"[RPi] ACK sent (seq={s}, executed={executed})")
                except Exception as e:
                    print(f"[RPi] COMMAND store error: {e}")
                    try:
                        ws.send(json.dumps({"type": "ACK", "of": "COMMAND", "ok": False, "error": str(e)}))
                    except Exception:
                        pass
                return

            if isinstance(message, str):
                print(f"[RPi] Unrecognized TEXT (not a command): {message[:200]}")
            else:
                print(f"[RPi] Unrecognized NON-TEXT message (len={len(message)})")

            ws.send("Message sent in invalid format. Accepted messages: 'SEND_PHOTO', 'TELEMETRY', 'PHOTO_WITH_TELEMETRY'")

        ws = websocket.WebSocketApp(
            args.server,
            on_open=lambda _ws: print("[RPi] WS open"),
            on_error=lambda _ws,e: print(f"[RPi] WS error: {e}"),
            on_close=lambda _ws,code,msg: print(f"[RPi] WS closed code={code} msg={msg}"),
            on_data=lambda _ws,data,opcode,fin: print(f"[RPi] on_data: {'text' if opcode==1 else 'binary' if opcode==2 else opcode}, len={len(data)}"),
            on_message=on_message
        )

        ws.run_forever()
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        if recording_started:
            print("[RPi] Stopping video recording...")
            picam2.stop_recording()
            print("[RPi] Video recording stopped.")
        picam2.stop()
        print("[RPi] Camera stopped.")

if __name__ == "__main__":
    main()