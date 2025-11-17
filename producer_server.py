import websocket
import pathlib
import subprocess
import os
import argparse
import json
import base64

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--server",  default=os.environ.get("SERVER_URL", "ws://127.0.0.1:8080"))
    p.add_argument("--capture", default=os.environ.get("CAPTURE_PY", "/app/capture.py"))
    p.add_argument("--img",     default=os.environ.get("IMG_DIR", "/img"))
    p.add_argument("--fname",   default=os.environ.get("FNAME", "photo.jpg"))
    p.add_argument("--width",   default=os.environ.get("WIDTH", 500))
    p.add_argument("--height",  default=os.environ.get("HEIGHT", 500))
    p.add_argument("--quality", default=os.environ.get("QUALITY", 90))
    return p.parse_args()

def main():
    args = parse_args()

    img_dir = pathlib.Path(args.img); img_dir.mkdir(parents=True, exist_ok=True)
    photo_path = str(img_dir / args.fname)

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
        elif message == "TELEMETRY":
            tmpl = json.load(open("telemetry.json"))
            # wczytywanie telemetrii z FC
            ws.send(json.dumps({"type": "TELEMETRY", "data": tmpl}))
        elif message == "PHOTO_WITH_TELEMETRY":
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
        else:
            ws.send("Message sent in invalid format. Accepted messages: 'SEND_PHOTO', 'TELEMETRY'")

    ws = websocket.WebSocketApp(
        args.server,
        on_message=on_message
    )
    ws.run_forever()

if __name__ == "__main__":
    main()
