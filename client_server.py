import asyncio, os, signal
from datetime import datetime 
import websockets

clients: set = set()
stop = asyncio.Event()

HOST, PORT = "0.0.0.0", 8080
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

async def handler(ws):
    peer = ws.remote_address
    clients.add(ws)
    print(f"[WS] connected: {peer}")
    await ws.send("SEND_PHOTO")  # ask the client to send a photo
    try:
        async for message in ws:
            # binary photo (if you switch client to send bytes)
            if isinstance(message, (bytes, bytearray)):
                file_name = f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                path = os.path.join(UPLOAD_DIR, file_name)
                with open(path, "wb") as f:
                    f.write(message)
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

    finally:
        clients.discard(ws)

def _signal_handler():
    if not stop.is_set():
        print("\n[WS] shutdown requested (signal). Closing clients…")
        stop.set()

async def stdin_sender():
    # Enter = wyślij SEND_PHOTO do wszystkich; 'q' = zamknij serwer
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        try:
            line = await loop.run_in_executor(None, input, "Enter=SEND_PHOTO, q=quit")
        except (EOFError, KeyboardInterrupt):
            break
        line = (line or "").strip().lower()
        if line == "q":
            _signal_handler()
            break
        dead = []
        for ws in list(clients):
            try:
                await ws.send("SEND_PHOTO")
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)
        print(f"[WS] SEND_PHOTO sent to {len(clients)} client(s)", flush=True)

async def main():
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass
    
    async with websockets.serve(
        handler,
        HOST, PORT,
        max_size=25 * 1024 * 1024,  # 25MB
    ):
        print(f"[WS] listening on ws://{HOST}:{PORT}")
        await stop.wait()
    
    if clients:
        await asyncio.gather(
            *[ws.close(code=1001, reason="server shutdown") for ws in list(clients)],
            return_exceptions=True
        )
    print("[WS] server stopped cleanly.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
