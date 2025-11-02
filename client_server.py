import asyncio, os, signal, json, time
from datetime import datetime 
import websockets
from typing import Dict
from prompt_generation.prompts import Prompts, PROMPT_FACTORIES
from collections import deque

NOTE_TIMEOUT_SEC = int(os.environ.get("NOTE_TIMEOUT_SEC", "15"))

clients: set = set()
stop = asyncio.Event()

HOST = os.environ.get("WS_HOST", "0.0.0.0")
PORT = int(os.environ.get("WS_PORT", "8080"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")
PROMPTS_DIR = os.environ.get("PROMPTS_DIR", "prompts")
COMMENTS_DIR = os.environ.get("COMMENTS_DIR", "comments")
MAX_WS_MB = int(os.environ.get("MAX_WS_MB", "25"))
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PROMPTS_DIR, exist_ok=True)

pending_notes = deque()

async def handler(ws):
    peer = ws.remote_address
    clients.add(ws)
    print(f"[WS] connected: {peer}")
    try:
        async for message in ws:
            # binary photo (if you switch client to send bytes)
            if isinstance(message, (bytes, bytearray)):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                file_base = f"img_{ts}"
                file_name = f"{file_base}.jpg"
                path = os.path.join(UPLOAD_DIR, file_name)
                with open(path, "wb") as f:
                    f.write(message)
                print(f"[WS] saved binary -> {path}")
                await ws.send(f"SAVED {path}")

                now = time.time()
                while pending_notes and pending_notes[0]["expires_at"] < now:
                    expired = pending_notes.popleft()
                    print(f"[WS] note expired (dropped): '{expired['text']}' queued_at={expired['queued_at']}")

                if pending_notes:
                    note = pending_notes.popleft()
                    note_txt = os.path.join(COMMENTS_DIR, f"{file_base}.note.txt")
                    note_json = os.path.join(COMMENTS_DIR, f"{file_base}.note.json")
                    with open(note_txt, "w", encoding="utf-8") as f: 
                        f.write(note["text"])
                    meta = {
                        "text": note["text"],
                        "queued_at": note["queued_at"],
                        "saved_at": ts,
                        "image_path": path,
                        "image_file": file_name,
                        "source": "BOTH",
                        "timeout_sec": NOTE_TIMEOUT_SEC
                    }
                    with open(note_json, "w", encoding="utf-8") as f:
                        json.dump(meta, f, ensure_ascii=False, indent=2)
                    print(f"[WS] note saved -> {note_txt} (+meta {note_json})")
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

def _generate_prompt(kind: str, kv: Dict[str, str]) -> Dict[str, str]:
    """
    kind: 'FS-1' lub 'FS-2'
    kv:   słownik z parametrami (object, glimpses, area)
    """
    # wartości domyślne
    params = {
        "object": kv.get("object", "helipad"),
        "glimpses": int(kv.get("glimpses", "6")),
        "area": int(kv.get("area", "80")),  # dla FS-1
    }
    t = Prompts(kind)
    factory = PROMPT_FACTORIES[t]
    if t == Prompts.FS1:
        text = factory(params["glimpses"], params["object"], params["area"])
    else:
        text = factory(params["glimpses"], params["object"])
    return {"kind": kind, "text": text, **params}

def _save_prompt(prompt_meta: Dict[str, str]) -> Dict[str, str]:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{prompt_meta['kind'].lower()}_{ts}"
    txt_path = os.path.join(PROMPTS_DIR, base + ".txt")
    json_path = os.path.join(PROMPTS_DIR, base + ".json")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(prompt_meta["text"])
    meta_to_save = dict(prompt_meta)
    meta_to_save.pop("text", None)
    meta_to_save["saved_at"] = ts
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta_to_save, f, ensure_ascii=False, indent=2)
    return {"txt": txt_path, "json": json_path}

# async def _send_prompt_to_client(prompt_text: str):
#     # bierzemy pierwszego aktywnego klienta (u Ciebie jest jeden)
#     ws = next(iter(clients), None)
#     if ws is None:
#         print("No drone connected – prompt zapisany lokalnie, ale nie wysłany.")
#         return
#     payload = {
#         "type": "SEND_PROMPT",
#         "msg_id": f"p-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
#         "prompt_id": f"p-{datetime.now().strftime('%Y%m%d%H%M%S')}",
#         "prompt": prompt_text
#     }
#     try:
#         await ws.send(json.dumps(payload))
#         print("[WS] SEND_PROMPT sent")
#     except Exception as e:
#         print(f"[WS] failed to send prompt: {e}")

async def stdin_repl():
    """
    Komendy:
      SEND_PHOTO           - poproś drona o zdjęcie
      BOTH <komentarz...>  - zapisz komentarz i poproś o zdjęcie
      PROMPT FS-1 [key=val ...]
      PROMPT FS-2 [key=val ...]
        Parametry:
          object=<nazwa>
          glimpses=<int>
          area=<int>        (tylko FS-1)
      q                     - zakończ
    """
    loop = asyncio.get_running_loop()
    print("Commands: SEND_PHOTO | BOTH <comment...> | PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..] | q")
    while not stop.is_set():
        try:
            line = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            line = "q"
        line = (line or " ").strip()
        if not line:
            continue

        cmd = line.lower()
        if cmd in ("q", "quit", "exit"):
            _signal_handler()
            break
        if cmd == "send_photo":
            ws = next(iter(clients), None)
            if ws is None:
                print("No drone connected")
                continue
            try:
                await ws.send("SEND_PHOTO")
                print("[WS] SEND_PHOTO sent")
            except Exception as e:
                print(f"[WS] send failed: {e}")
            continue

        if cmd.startswith("prompt "):
            parts = cmd.split()
            if len(parts) < 2:
                print("Usage: PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..]")
                continue
            kind = parts[1].upper()
            if kind not in ("FS-1", "FS-2"):
                print("Kind must be FS-1 or FS-2")
                continue

            kv: Dict[str, str] = {}
            for token in parts[2:]:
                if "=" in token:
                    k, v = token.split("=", 1)
                    kv[k.strip().lower()] = v.strip()
            meta = _generate_prompt(kind, kv)
            saved = _save_prompt(meta)
            print(f"[PROMPT] saved -> {saved['txt']} (+meta {saved['json']})")
            continue
        
        if cmd.startswith("both"):
            comment = line[4:].strip()
            if not comment:
                print("Usage: BOTH <comment text>")
                continue

            ws = next(iter(clients), None)
            if ws is None:
                print("No drone connected - not queuing comment")
                continue 

            try:
                await ws.send("SEND_PHOTO")
                queued_at = datetime.now().strftime("%Y%m%d_%H%M%S")
                pending_notes.append({"text": comment, "queued_at": queued_at, "expires_at": time.time() + NOTE_TIMEOUT_SEC})
                print(f"[WS] SEND_PHOTO sent (comment queued for {NOTE_TIMEOUT_SEC}s): '{comment}')")
            except Exception as e: 
                print(f"[WS] send failed, comment NOT queued: {e}")
            continue 
        
        else:
            print("Commands: SEND_PHOTO | BOTH <comment...> | PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..] | q")

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
