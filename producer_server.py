import websocket
import pathlib
import subprocess
import os
import argparse

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--server", default=os.environ.get("SERVER_URL", "ws://127.0.0.1:8080"))
    p.add_argument("--capture", default=os.environ.get("CAPTURE_PY", "/app/capture.py"))
    p.add_argument("--out", default=os.environ.get("OUT_DIR", "/out"))
    p.add_argument("--fname", default=os.environ.get("FNAME", "photo.jpg"))
    return p.parse_args()

def main():
    args = parse_args()

    out_dir = pathlib.Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    photo_path = str(out_dir / args.fname)

    def take_photo():
        env = os.environ.copy()
        env["OUT_DIR"] = str(out_dir)
        env["FNAME"] = args.fname
        subprocess.run(["python3", args.capture], env=env, check=True)

    def on_message(ws, message):
        print("Received:", message)
        if message == "SEND_PHOTO":
            take_photo()
            with open(photo_path, "rb") as f:
                ws.send(f.read(), opcode=websocket.ABNF.OPCODE_BINARY)
                print(f"Sent photo: {photo_path}")
        elif message.startswith("Coordinates: "):
            print(message)
            ws.send("Coordinates received.")
        else:
            ws.send("Message send in invalid format. Accepted messages: 'SEND_PHOTO', 'Coordinates: (lat, lon)'")

    ws = websocket.WebSocketApp(
        args.server,
        on_message=on_message
    )
    ws.run_forever()

if __name__ == "__main__":
    main()
