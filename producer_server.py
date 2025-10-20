import websocket
import threading
import base64

SERVER_URL = "ws://server_address:8080"

def on_message(ws, message):
    print("Received:", message)
    if message == "SEND_PHOTO":
        #os.system("libcamera-jpeg -o photo.jpg")
        with open("photo.jpg", "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
            ws.send(data)
            print("New photo sent.")
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
