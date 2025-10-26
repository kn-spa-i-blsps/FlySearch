import websocket
import pathlib
import subprocess
import os
import argparse

def parse_args():
    p = argparse.ArgumentParser()
    server = os.environ.get("SERVER_URL", p.server)
    capture = os.environ.get("CAPTURE_PY", p.capture)
    img_dir = pathlib.Path(os.environ.get("OUT_DIR", p.img))
    fname = os.environ.get("FNAME", p.fname)
    w = int(os.environ.get("WIDTH", "1920"))
    h = int(os.environ.get("HEIGHT", "1080"))
    q = int(os.environ.get("QUALITY", "90"))
    return p.parse_args()

def main():
    args = parse_args()

    img_dir = pathlib.Path(args.img); img_dir.mkdir(parents=True, exist_ok=True)
    photo_path = str(img_dir / args.fname)

    def take_photo():
        env = os.environ.copy()
        env["IMG_DIR"] = str(img_dir)
        env["FNAME"] = args.fname
        env["WIDTH"] = args.w
        env["HEIGHT"] = args.h 
        env["QUALITY"] = args.q
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
