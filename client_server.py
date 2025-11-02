import asyncio, os, signal
from datetime import datetime 
import websockets

clients: set = set()
stop = asyncio.Event()

HOST = os.environ.get("WS_HOST", "0.0.0.0")
PORT = int(os.environ.get("WS_PORT", "8080"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")
MAX_WS_MB = int(os.environ.get("MAX_WS_MB", "25"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

async def handler(ws):
    peer = ws.remote_address
    clients.add(ws)
    print(f"[WS] connected: {peer}")
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

async def stdin_repl():
    # SEND_PHOTO - wyślij zdjęcie na drona; 'q' = zamknij serwer
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        try:
            line = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            line = "q"
        line = (line or " ").strip().lower()
        if line in ("q", "quit", "exit"):
            _signal_handler()
            break
        if line == "send_photo":
            ws = next(iter(clients), None)
            if ws is None:
                print("No drone connected")
                continue
            try:
                await ws.send("SEND_PHOTO")
                print("[WS] SEND_PHOTO sent")
            except Exception as e:
                print(f"[WS] send failed: {e}")
        else:
            print("Commands: SEND_PHOTO | q")

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
        max_size=MAX_WS_MB * 1024 * 1024,  # 25MB
    ):
        print(f"[WS] listening on ws://{HOST}:{PORT}")
        await asyncio.gather (stdin_repl(), stop.wait())
    
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
