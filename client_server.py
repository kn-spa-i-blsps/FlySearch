import asyncio, os, signal, json, time
from datetime import datetime
from pathlib import Path
import websockets
from typing import Dict

from google.api_core.exceptions import InvalidArgument

from prompt_generation.prompts import Prompts, PROMPT_FACTORIES
from collections import deque
from PIL import Image
import google.generativeai as genai
import base64

import add_guardrails as gd

NOTE_TIMEOUT_SEC = int(os.environ.get("NOTE_TIMEOUT_SEC", "15"))

clients: set = set()
stop = asyncio.Event()

HOST = os.environ.get("WS_HOST", "0.0.0.0")
PORT = int(os.environ.get("WS_PORT", "8080"))
CHATS_DIR = Path(os.environ.get("CHATS_DIR", "saved_chats"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")
PROMPTS_DIR = os.environ.get("PROMPTS_DIR", "prompts")
TELEMETRY_DIR = os.environ.get("TELEMETRY_DIR", "telemetry")
MAX_WS_MB = int(os.environ.get("MAX_WS_MB", "25"))
API_KEY = os.environ.get("API_KEY", "key")
if not API_KEY:
    print("FATAL ERROR: API_KEY environment variable not set.")
    exit(1)

os.makedirs(CHATS_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PROMPTS_DIR, exist_ok=True)
os.makedirs(TELEMETRY_DIR, exist_ok=True)

# Global variables for VLM communication (only GEMINI for now)
# TODO: ability to choose other ai models.
model = None
chat_session = None

# FUTURE: More than one drone (so more than one chat, more than one cache etc.)
# Cache of last saved photo, telemetry and prompt (for easy access)
last_photo_path_cache = None
last_telemetry_path_cache = None
last_prompt_text_cache = None

# TODO IMPORTANT: error handling, backing changes when error occurs
# TODO TWEAKS: arrow up = last command

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
                continue

            # Change the message to json.
            text = message.strip()
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue

            # Telemetry in json.
            if isinstance(obj, dict) and obj.get("type") == "TELEMETRY":
                data = obj.get("data", {})
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                file_base = f"telemetry_{ts}"
                file_name = f"{file_base}.json"
                path = os.path.join(TELEMETRY_DIR, file_name)

                payload = {
                    "received_at": ts,
                    "associated_photo": None,
                    "data": data
                }

                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                print(f"[WS] saved telemetry(JSON) -> {path}")
                await ws.send(f"SAVED {path}")
                continue

            # This command is used to get data for the VLM.
            if isinstance(obj, dict) and obj.get("type") == "PHOTO_WITH_TELEMETRY":
                global last_photo_path_cache
                global last_telemetry_path_cache

                ts = datetime.now().strftime("%Y%m%d_%H%M%S")

                photo_base64 = obj.get("photo")
                telemetry_data = obj.get("telemetry", {})

                # Photo
                if not photo_base64:
                    print("[WS] 'PHOTO_WITH_TELEMETRY' received but 'photo' field is missing.")
                    await ws.send("ERROR: Photo data missing in combined message.")
                    continue

                try:
                    photo_data = base64.b64decode(photo_base64)
                except TypeError as e:
                    print(f"[WS] Failed to decode Base64 photo data: {e}")
                    await ws.send("ERROR: Invalid photo data encoding.")
                    continue

                img_file_base = f"img_{ts}"
                img_file_name = f"{img_file_base}.jpg"
                img_path = os.path.join(UPLOAD_DIR, img_file_name)

                with open(img_path, "wb") as f:
                    f.write(photo_data)

                print(f"[WS] saved photo -> {img_path}")

                # Telemetry
                tel_file_base = f"telemetry_{ts}"
                tel_file_name = f"{tel_file_base}.json"
                tel_path = os.path.join(TELEMETRY_DIR, tel_file_name)

                # We are caching paths for easier access when sending to VLM.
                last_photo_path_cache = img_path
                last_telemetry_path_cache = tel_path

                payload = {
                    "received_at": ts,
                    "associated_photo": img_file_name,
                    "data": telemetry_data
                }

                with open(tel_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                print(f"[WS] saved telemetry -> {tel_path}")

                await ws.send(f"SAVED {img_path} AND {tel_path}")
                continue

            continue


    except websockets.ConnectionClosed:
        print(f"[WS] disconnected: {peer}")
        
    except Exception as e:
        print(f"[WS] error: {e}")

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

    global last_prompt_text_cache
    last_prompt_text_cache = prompt_meta["text"]

    json_path = os.path.join(PROMPTS_DIR, base + ".json")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(prompt_meta["text"])
    meta_to_save = dict(prompt_meta)
    meta_to_save.pop("text", None)
    meta_to_save["saved_at"] = ts
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta_to_save, f, ensure_ascii=False, indent=2)
    return {"txt": txt_path, "json": json_path}

# Parse telemetry from json to the prompt, that will be added to the photo.
def parse_telemetry(path):
    with open(path, "r", encoding="utf-8") as f:
        telemetry = json.load(f)

    telemetry_data = telemetry.get("data", {})
    height = telemetry_data.get("position", {}).get("alt", "N/A")
    return [f"Your current altitude is {height} meters above ground level.", height]

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
    global model
    global chat_session
    global last_prompt_text_cache
    global last_photo_path_cache
    global last_telemetry_path_cache

    loop = asyncio.get_running_loop()
    print("Commands: PHOTO_WITH_TELEMETRY | SEND_PHOTO | TELEMETRY | PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..] | q")
    print("          CHAT_INIT | CHAT_DELETE | CHAT_SAVE <name> | CHAT_RETRIEVE <name> | SEND_TO_VLM")
    while not stop.is_set():
        try:
            line = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            line = "q"
        line = (line or " ").strip()
        if not line:
            continue

        cmd = line.lower()

        # Close the server.
        if cmd in ("q", "quit", "exit"):
            _signal_handler()
            break

        """
        There are three easy commands, that are just passed to the drone.
        The most important is 'photo_with_telemetry'. It is used to request
        from the drone data that will be passed to the VLM. Other two are 
        additional for monitoring the drone. 
        """
        if cmd in ["send_photo", "telemetry", "photo_with_telemetry"]:
            ws = next(iter(clients), None)
            if ws is None:
                print("No drone connected")
                continue
            try:
                await ws.send(cmd.upper())
                print(f"[WS] {cmd.upper()} sent")
            except Exception as e:
                print(f"[WS] send failed: {e}")
            continue

        # use after photo_with_telemetry, after when photo and telemetry are human-checked.
        if cmd == "send_to_vlm":

            if chat_session is None:
                print("Chat with vlm is not initialized. Use CHAT_INIT first.")
                continue

            if last_photo_path_cache is None or last_telemetry_path_cache is None:
                print("No photo or telemetry cached - it may be because no photo/telemetry was requested yet.")
                continue

            try:
                prompt = parse_telemetry(last_telemetry_path_cache)
            except FileNotFoundError:
                print(f"Error: No telemetry found '{last_telemetry_path_cache}'. Data may be deleted.")
                continue
            except Exception as e:
                print(f"Error during telemetry opening: {e}")
                continue

            try:
                img = Image.open(last_photo_path_cache)
                img = gd.dot_matrix_two_dimensional_drone(
                    img=img,
                    w_dots=5,
                    h_dots=5,
                    drone_height=prompt[1]
                )
            except FileNotFoundError:
                print(f"Error: No photo found '{last_photo_path_cache}'. Photo may be deleted.")
                continue
            except Exception as e:
                print(f"Error during photo opening: {e}")
                continue

            try:
                response = chat_session.send_message([prompt[0], img])
            except Exception as e:
                print(f"Error when talking to vlm: {e}")
                continue

            print(response.text)

            # TODO: autosave

            continue

        # start the conversation with the vlm, send the initial prompt
        if cmd.startswith("chat_init"):

            if model is None:
                print("Model not initialized! This shouldn't happen...")
                continue

            if chat_session is not None:
                print("Chat already exists. Use CHAT_DELETE to delete the chat first.")
                continue

            if last_prompt_text_cache is None:
                print("No prompt generated yet. Use PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..].")
                continue

            # As it turns out, we need the photo and the telemtry with the initial prompt.
            if last_photo_path_cache is None or last_telemetry_path_cache is None:
                print("No photo or telemetry cached - it may be because no photo/telemetry was requested yet.")
                continue

            try:
                chat_session = model.start_chat()
            except Exception as e:
                print(f"Initialization of the chat failed: {e}")
                continue

            try:
                prompt = parse_telemetry(last_telemetry_path_cache)
            except FileNotFoundError:
                print(f"Error: No telemetry found '{last_telemetry_path_cache}'. Data may be deleted.")
                continue
            except Exception as e:
                print(f"Error during telemetry opening: {e}")
                continue

            try:
                img = Image.open(last_photo_path_cache)
                # TODO: Magic numbers to fix
                img = gd.dot_matrix_two_dimensional_drone(
                    img=img,
                    w_dots=5,
                    h_dots=5,
                    drone_height=prompt[1]
                )
            except FileNotFoundError:
                print(f"Error: No photo found '{last_photo_path_cache}'. Photo may be deleted.")
                continue
            except Exception as e:
                print(f"Error during photo opening: {e}")
                continue

            try:
                response = chat_session.send_message([last_prompt_text_cache, img, prompt[0]])
            except InvalidArgument as e:
                print(f"ERROR: Invalid api key: {e}")
                chat_session = None
                continue
            except Exception as e:
                print(f"Message sending failed: {e}")
                chat_session = None
                continue

            print(f"VLM answer: {response.text}")

            # TODO: autosave

            continue

        # Save the chat history. Usage: CHAT_SAVE <name_of_the_chat>
        if cmd.startswith("chat_save "):

            if chat_session is None:
                print("Chat with vlm is not initialized. Use CHAT_INIT first.")
                continue

            cmd = cmd.split()
            if len(cmd) != 2:
                print("Usage: CHAT_SAVE <name_of_the_chat>")
                continue
            chat_id = cmd[1]

            chat_dir = CHATS_DIR / chat_id
            assets_dir = chat_dir / "assets"

            assets_dir.mkdir(parents=True, exist_ok=True)

            serializable_history = []
            image_counter = 0

            for content in chat_session.history:
                serializable_content = {
                    "role": content.role,
                    "parts": []
                }

                for part in content.parts:
                    # Text saving.
                    if part.text:
                        serializable_content["parts"].append({
                            "type": "text",
                            "data": part.text
                        })
                    # Photo saving.
                    elif part.inline_data:
                        blob = part.inline_data

                        # Ustal rozszerzenie pliku
                        mime_type = blob.mime_type
                        ext = mime_type.split('/')[-1]
                        if ext == 'jpeg':
                            ext = 'jpg'  # Normalization

                        # Stwórz unikalną nazwę pliku
                        filename = f"image_{image_counter}.{ext}"
                        file_path = assets_dir / filename

                        # Zapisz obraz (bajty) do pliku
                        try:
                            file_path.write_bytes(blob.data)
                        except Exception as e:
                            print(f"Error writing image file {file_path}: {e}")
                            continue

                        # Save RELATIVE path to the photo in json.
                        relative_path = str(assets_dir.name + "/" + filename)  # np. "assets/image_0.png"
                        serializable_content["parts"].append({
                            "type": "image",
                            "path": relative_path
                        })
                        image_counter += 1

                serializable_history.append(serializable_content)

            # Save final json file
            json_path = chat_dir / "history.json"
            try:
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(serializable_history, f, indent=2, ensure_ascii=False)
                print(f"Successfully saved history to {json_path}")
                print(f"Successfully saved {image_counter} images to {assets_dir}")
            except Exception as e:
                print(f"Error writing JSON file {json_path}: {e}")
            continue

        # Continue stopped conversation. Usage: CHAT_RETRIEVE <chat_name>
        if cmd.startswith("chat_retrieve "):

            if chat_session is None:
                print("Chat with vlm is not initialized. Use CHAT_INIT first.")
                continue

            cmd = cmd.split()
            if len(cmd) != 2:
                print("Usage: CHAT_SAVE <name_of_the_chat>")
                continue
            chat_id = cmd[1]
            chat_dir = CHATS_DIR / chat_id
            json_path = chat_dir / "history.json"

            if not json_path.exists():
                print(f"No history file found for chat_id '{chat_id}' at {json_path}")
                continue

            rebuilt_history = []

            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    serializable_history = json.load(f)
            except Exception as e:
                print(f"Error reading JSON file {json_path}: {e}")
                continue

            for content_data in serializable_history:
                role = content_data["role"]
                rebuilt_parts = []

                for part_data in content_data["parts"]:
                    # Retrieve text
                    if part_data["type"] == "text":
                        rebuilt_parts.append(part_data["data"])

                    # Retrieve photo
                    elif part_data["type"] == "image":
                        relative_path = part_data["path"]
                        image_path = chat_dir / relative_path

                        if image_path.exists():
                            try:
                                # Load image to used by the VLM format.
                                img = Image.open(image_path)
                                rebuilt_parts.append(img)
                            except Exception as e:
                                print(f"Error loading image {image_path}: {e}")
                        else:
                            print(f"Warning: Image file not found at {image_path}")

                rebuilt_history.append({
                    "role": role,
                    "parts": rebuilt_parts
                })
            chat_session = model.start_chat(history=rebuilt_history)
            print(f"Successfully loaded chat '{chat_id}'")

            continue

        # Delete the conversation.
        if cmd.startswith("chat_delete"):
            print("Are you sure you want to delete this chat? You can use CHAT_SAVE to save it first.")
            print("Type 'yes' to delete.")

            try:
                ans = await loop.run_in_executor(None, input, "> ")
            except (EOFError, KeyboardInterrupt):
                ans = "no"

            if ans.lower() == "yes":
                chat_session = None
                print("Chat deleted.")
            else:
                print("Chat not deleted.")
            continue

        # Generate prompt of given kind and with given parameters.
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
            try:
                meta = _generate_prompt(kind, kv)
                saved = _save_prompt(meta)
            except Exception as e:
                print(f"Error in _generate_prompt or _save_prompt: {e}")
                continue

            print(f"[PROMPT] saved -> {saved['txt']} (+meta {saved['json']})")
            continue

        else:
            print("Commands: PHOTO_WITH_TELEMETRY | SEND_PHOTO | TELEMETRY | PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..] | q")
            print("          CHAT_INIT | CHAT_DELETE | CHAT_SAVE <name> | CHAT_RETRIEVE <name> | SEND_TO_VLM")

async def main():
    global model
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        genai.configure(api_key=API_KEY)
    except Exception as e:
        print(f"Error when initializing model: {e}")
        exit(1)
    
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
