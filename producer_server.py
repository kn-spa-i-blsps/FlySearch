import websocket
import threading

SERVER_URL = "ws://server_address:8080"
CAPTURE_PY = "/home/app/capture.py"
OUT_DIR = "./img"
FNAME = "photo.jpg"

pathlib.Path(OUT_DIR).mkdir(parents=True, exist_ok=True)
PHOTO_PATH = str(pathlib.Path(OUT_DIR) / FNAME)

def take_photo():
    env = os.environ.copy()
    env["OUT_DIR"] = str(pathlib.Path(OUT_DIR).resolve())
    env["FNAME"] = FNAME
    subprocess.run(["python3", CAPTURE_PY], env=env, check=True)

def on_message(ws, message):
    print("Received:", message)
    if message == "SEND_PHOTO":
        take_photo()
        with open(PHOTO_PATH, "rb") as f:
            ws.send(f.read(), opcode=websocket.ABNF.OPCODE_BINARY)
            print(f"Sent photo: {PHOTO_PATH}")
    elif message.startswith("Coordinates: "):
        print(message)
        ws.send("Coordinates received.")
    else:
        ws.send("Message send in invalid format. Accepted messages: 'SEND_PHOTO', 'Coordinates: (lat, lon)'")

def run_client():
    ws = websocket.WebSocketApp(
        SERVER_URL,
        on_message=on_message
    )
    ws.run_forever()

threading.Thread(target=run_client).start()
