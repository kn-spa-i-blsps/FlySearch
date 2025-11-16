import websocket, pathlib, subprocess, os, argparse, json, base64, uuid 
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
        preview = message if isinstance(message, str) else f"<{len(message)} bytes>"
        print("Received:", (preview[:160] + "...") if isinstance(preview, str) and len(preview) > 160 else preview)

        if message == "SEND_PHOTO":
            take_photo()
            with open(photo_path, "rb") as f:
                ws.send(f.read(), opcode=websocket.ABNF.OPCODE_BINARY)
            print(f"Sent photo: {photo_path}")
            return

        elif message == "TELEMETRY":
            try:
                with open("telemetry.json", "r", encoding="utf-8") as tf:
                    tmpl = json.load(tf)
            except FileNotFoundError:
                print("[RPi] telemetry.json not found – sending empty {}")
                tmpl = {}
            ws.send(json.dumps({"type": "TELEMETRY", "data": tmpl}))
            print("[RPi] Sent TELEMETRY json")
            return

        elif message == "PHOTO_WITH_TELEMETRY":
            take_photo()
            with open(photo_path, "rb") as f:
                photo_data = f.read()
            photo_base64 = base64.b64encode(photo_data).decode('utf-8')

            try:
                with open("telemetry.json", "r", encoding="utf-8") as tf:
                    tmpl = json.load(tf)
            except FileNotFoundError:
                print("[RPi] telemetry.json not found – embedding empty {}")
                tmpl = {}

            payload = {
                "type": "PHOTO_WITH_TELEMETRY",
                "photo": photo_base64,
                "telemetry": tmpl
            }
            ws.send(json.dumps(payload))
            print(f"Sent photo ({photo_path}) with telemetry.")
            return

        # --- JSON control plane: COMMAND from server ---
        obj = None
        if isinstance(message, str):
            try:
                obj = json.loads(message)
            except Exception:
                obj = None
                print(f"[RPi] json.loads FAILED on text message: {e}")

        if isinstance(obj, dict) and obj.get("type") == "COMMAND":
            try:
                s = next_seq()
                record = {
                    "ts": now_ts(),
                    "seq": s,
                    "direction": "in",
                    "kind": "COMMAND",
                    "payload": obj,
                }
                append_jsonl(session_file, record)
                write_json(latest_file, record)
                if "move" in obj:
                    x, y, z = obj["move"]
                    print(f"[RPi] COMMAND odebrano: MOVE (x={x}, y={y}, z={z})")
                elif obj.get("action") == "FOUND":
                    print("[RPi] COMMAND odebrano: FOUND")
                else:
                    print(f"[RPi] COMMAND odebrano: {obj}")

                print(f"[RPi] COMMAND stored (seq={s}) → {session_file.name}; latest_command.json updated")
                ws.send(json.dumps({"type": "ACK", "of": "COMMAND", "ok": True, "seq": s}))
                print(f"[RPi] ACK wysłany (seq={s})")
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
        on_message=on_message
    )
    ws.run_forever()

if __name__ == "__main__":
    main()
