import websocket
import pathlib
import subprocess
import os
import argparse
import json
import base64
import uuid 
from datetime import datetime 

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--server",  default=os.environ.get("SERVER_URL", "ws://127.0.0.1:8080"))
    p.add_argument("--capture", default=os.environ.get("CAPTURE_PY", "/app/capture.py"))
    p.add_argument("--img",     default=os.environ.get("IMG_DIR", "/img"))
    p.add_argument("--fname",   default=os.environ.get("FNAME", "photo.jpg"))
    p.add_argument("--width",   default=os.environ.get("WIDTH", 1920))
    p.add_argument("--height",  default=os.environ.get("HEIGHT", 1080))
    p.add_argument("--quality", default=os.environ.get("QUALITY", 90))
    p.add_argument("--commands", default=os.environ.get("COMMANDS_DIR", "/commands"))
    return p.parse_args()

def main():
    args = parse_args()

    img_dir = pathlib.Path(args.img); img_dir.mkdir(parents=True, exist_ok=True)
    commands_dir = pathlib.Path(args.commands); commands_dir.mkdir(parents=True, exist_ok=True)
    photo_path = str(img_dir / args.fname)

    shortid = uuid.uuid4().hex[:8]
    session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{shortid}"
    session_file = commands_dir / f"session_{session_id}.jsonl"
    latest_file  = commands_dir / "latest_command.json"

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

    def take_photo():
        env = os.environ.copy()
        env["IMG_DIR"] = str(img_dir)
        env["FNAME"] = args.fname
        env["WIDTH"] = str(args.width)
        env["HEIGHT"] = str(args.height)
        env["QUALITY"] = str(args.quality)
        subprocess.run(["python3", args.capture], env=env, check=True)

    def on_message(ws, message):
        print("Received:", message)
        if message == "SEND_PHOTO":
            take_photo()
            with open(photo_path, "rb") as f:
                ws.send(f.read(), opcode=websocket.ABNF.OPCODE_BINARY)
                print(f"Sent photo: {photo_path}")
        if message == "TELEMETRY":
            tmpl = json.load(open("telemetry.json"))
            # wczytywanie telemetrii z FC
            ws.send(json.dumps({"type": "TELEMETRY", "data": tmpl}))
        if message == "PHOTO_WITH_TELEMETRY":
            # Base64, because we can't combine binary data with text.
            take_photo()
            with open(photo_path, "rb") as f:
                photo_data = f.read()
            photo_base64 = base64.b64encode(photo_data).decode('utf-8')

            tmpl = json.load(open("telemetry.json"))

            payload = {
                "type": "PHOTO_WITH_TELEMETRY",
                "photo": photo_base64,
                "telemetry": tmpl
            }

            ws.send(json.dumps(payload))
            print(f"Sent photo ({photo_path}) with telemetry.")

                    # --- JSON control plane: COMMAND from server ---
        try:
            obj = json.loads(message) if isinstance(message, str) else None
        except Exception:
            obj = None

        if isinstance(obj, dict) and obj.get("type") == "COMMAND":
            try:
                s = next_seq()
                record = {
                    "ts": now_ts(),
                    "seq": s,
                    "direction": "in",
                    "kind": "COMMAND",
                    "payload": obj,     # np. {"type":"COMMAND","move":[x,y,z]} lub {"type":"COMMAND","action":"FOUND"}
                }
                append_jsonl(session_file, record)
                write_json(latest_file, record)
                print(f"[RPi] COMMAND stored (seq={s}) → {session_file.name}; latest_command.json updated")

                # ACK z powrotem na serwer
                ws.send(json.dumps({"type": "ACK", "of": "COMMAND", "ok": True, "seq": s}))
            except Exception as e:
                print(f"[RPi] COMMAND store error: {e}")
                try:
                    ws.send(json.dumps({"type": "ACK", "of": "COMMAND", "ok": False, "error": str(e)}))
                except Exception:
                    pass
            return

        else:
            ws.send("Message sent in invalid format. Accepted messages: 'SEND_PHOTO', 'TELEMETRY'")

    ws = websocket.WebSocketApp(
        args.server,
        on_message=on_message
    )
    ws.run_forever()

if __name__ == "__main__":
    main()
