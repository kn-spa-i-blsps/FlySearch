import asyncio, os
from datetime import datetime 
import websockets

HOST, PORT = "0.0.0.0", 8080
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

async def handler(ws):
    peer = ws.remote_address
    print(f"[WS] connected: {peer}")
    await ws.send("SEND_PHOTO")  # ask the client to send a photo
    try:
        async for message in ws:
            # binary photo (if you switch client to send bytes)
            if isinstance(message, (bytes, bytearray)):
                data = bytes(message)
                file_name = f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                path = os.path.join(UPLOAD_DIR, file_name)
                with open(path, "wb") as f:
                    f.write(data)
                print(f"[WS] saved binary -> {path}")
                await ws.send(f"SAVED {path}")
                continue

            # text messages
            text = message.strip()
            if text.startswith("Coordinates: "):
                print(f"[WS] coordinates: {text}")
                await ws.send("Coordinates received")
                continue

    except websockets.ConnectionClosed:
        print(f"[WS] disconnected: {peer}")
        
    except Exception:
        print("Invalid message type")

async def main():
    async with websockets.serve(
        handler,
        HOST, PORT,
        max_size=25 * 1024 * 1024,  # 25MB
    ):
        print(f"[WS] listening on ws://{HOST}:{PORT}")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
