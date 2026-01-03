import asyncio, os, signal, json, time
from datetime import datetime
from pathlib import Path
import websockets
from typing import Dict
import io

from google.api_core.exceptions import InvalidArgument

from conversation.abstract_conversation import Role
from conversation.conversations import LLM_BACKEND_FACTORIES
from prompt_generation.prompts import Prompts, PROMPT_FACTORIES
from response_parsers.xml_response_parser import parse_xml_response, ParsingError
from collections import deque
from PIL import Image
import google.generativeai as genai
import base64

import add_guardrails as gd

NOTE_TIMEOUT_SEC = int(os.environ.get("NOTE_TIMEOUT_SEC", "15"))

clients: set = set()
stop = asyncio.Event()

MODEL_BACKEND = os.environ.get("MODEL_BACKEND", "gemini")
MODEL_NAME = os.environ.get("MODEL_NAME", "gemini-2.5-flash")
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

collision_warning_str = "Your move would cause a collision. Make other move."

# Global variable for VLM communication
conversation = None

# FUTURE: More than one drone (so more than one chat, more than one cache etc.)
# Cache of last saved photo, telemetry and prompt (for easy access)
last_photo_path_cache = None
last_telemetry_path_cache = None
last_prompt_text_cache = None

# Handle receiving messages.
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

            if isinstance(obj, dict) and obj.get("type") == "ACK" and obj.get("of") == "COMMAND":
                print(f"[ACK ← RPi] COMMAND seq={obj.get('seq')} ok={obj.get('ok')} err={obj.get('error')}")
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


                try:
                    img=Image.open(io.BytesIO(photo_data))
                    w, h = img.size
                    side = min(w,h)
                    left = (w - side)// 2
                    top = (h - side) //2
                    right = left + side
                    bottom = top + side

                    img_cropped=img.crop((left,top,right,bottom))

                    img_cropped.save(img_path, format="JPEG", quality=90)
                    print(f"[WS] saved *square* photo -> {img_path} ({side}x{side})")
                except Exception as e:
                    print(f"[WS] square crop failed, saving raw photo: {e}")
                    with open(img_path, "wb") as f:
                        f.write(photo_data)
                        print(f"[WS] saved photo (raw) -> {img_path}")
                    

              
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
    height = telemetry_data.get("position", {}).get("alt", 10)
    return [f"Your current altitude is {height} meters above ground level.", height]

async def _send_command_to_client(*, found: bool = False, move=None) -> bool:
    """
    Wyślij komendę do RPi:
      - found=True  ->  {"type":"COMMAND","action":"FOUND", ...}
      - move=(x,y,z)->  {"type":"COMMAND","move":[x,y,z], ...}
    Zwraca True/False w zależności od powodzenia wysyłki.
    """
    ws = next(iter(clients), None)
    if ws is None:
        print("[WS] No drone connected - command NOT sent")
        return False

    payload = {
        "type": "COMMAND",
        "ts": datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
    }

    if found:
        payload["action"] = "FOUND"
    elif move is not None:
        try:
            x, y, z = float(move[0]), float(move[1]), float(move[2])
        except Exception as e:
            print(f"[WS] Invalid move triple {move}: {e}")
            return False
        payload["move"] = [x, y, z]
    else:
        print("[WS] No command content (neither 'found' nor 'move'). Not sending.")
        return False

    try:
        await ws.send(json.dumps(payload))
        print("[WS] COMMAND sent to RPi")
        return True
    except Exception as e:
        print("[WS] COMMAND send failed:", e)
        return False

async def _confirm_send(move=None, found=False):
    print("\n--- COMMAND PREVIEW ---")
    if found:
        print("ACTION: FOUND")
        return 3
    else:
        x, y, z = move
        print(f"MOVE: (x={x}, y={y}, z={z})")
    print("Press Enter to send, or type 'no' to cancel.")
    loop = asyncio.get_running_loop()
    try:
        ans = await loop.run_in_executor(None, input, "> ")
    except (EOFError, KeyboardInterrupt):
        ans = "no"

    if ans.strip().lower() in ("", "y", "yes"):
        return 1
    elif ans.strip().lower() in ("w", "warning"):
        return 2
    else:
        return 0

''' --------------------------- CLIENT SERVICES -------------------------- '''

async def search(kind, kv, loop):
    """
    Orchestrates an automated search test sequence.

    This function handles the end-to-end flow: generating the initial prompt,
    sending commands to the drone, initializing the VLM chat, and entering
    a loop to process visual feedback until the 'glimpses' limit is reached
    or the object is found or the test is aborted.

    The user is expected to validate the VLM's decisions during the process
    (accept, report collision, or stop).
    """
    print("\n--- SEARCHING... ---")
    prompt_generation(kind, kv)
    await send_message("photo_with_telemetry")
    ret = await chat_init()
    chat_save("autosave")
    count = 1
    while ret not in {0, 3} and count != kv["glimpses"]:
        await send_message("photo_with_telemetry")
        ret = await send_to_vlm()
        chat_save("autosave")
        count += 1

async def send_message(cmd):
    """
    Transmits a command to the connected drone via WebSocket.

    Selects the first available client from the 'clients' collection.
    """
    ws = next(iter(clients), None)
    if ws is None:
        print("No drone connected!")
        return
    try:
        await ws.send(cmd.upper())
        print(f"[WS] {cmd.upper()} sent")
    except Exception as e:
        print(f"[WS] send failed: {e}")

async def collision_warning():
    """
    Triggers a collision warning context update to the VLM.
    Used when the operator deems a move risky.
    """
    await send_to_vlm(is_warning=True)

async def send_to_vlm(is_init=False, is_warning=False):
    """
    Prepares and sends the current context (image, telemetry, prompts) to the Vision Language Model.

    Args:
        is_init (bool): If True, initializes a new chat session with the system prompt.
        is_warning (bool): If True, injects a collision warning prompt to force a corrective decision.

    Flow:
    1. Validates global state (model, chat session, cached data).
    2. Processes the cached telemetry and image (applying grid overlays).
    3. Constructs the payload (Text + Image) and calls the API.
    4. Parses the XML response from the VLM.
    5. requests operator confirmation before executing the suggested move.
    """
    global conversation
    global last_prompt_text_cache
    global last_photo_path_cache
    global last_telemetry_path_cache
    ret = -1
    # --- Chat Initialization Checks ---
    if is_init:
        if conversation is not None:
            print("Chat already exists. Use CHAT_DELETE to delete the chat first.")
            return ret # ERR

        if last_prompt_text_cache is None:
            print("No prompt generated yet. Use PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..].")
            return ret # ERR

        factory = LLM_BACKEND_FACTORIES[MODEL_BACKEND](MODEL_NAME)
        conversation = factory.get_conversation()
        conversation.begin_transaction(Role.USER)
    else:
        if conversation is None:
            print("Chat with vlm is not initialized. Use CHAT_INIT first.")
            return ret # ERR

    # --- Data Availability Checks ---
    if last_photo_path_cache is None or last_telemetry_path_cache is None:
        print("No photo or telemetry cached - it may be because no photo/telemetry was requested yet.")
        return ret # ERR

    # --- Telemetry Processing ---
    try:
        telemetry_data = parse_telemetry(last_telemetry_path_cache)
        telemetry_prompt_text = telemetry_data[0]
        drone_height = telemetry_data[1]
    except FileNotFoundError:
        print(f"Error: No telemetry found '{last_telemetry_path_cache}'. Data may be deleted.")
        return ret # ERR
    except Exception as e:
        print(f"Error during telemetry opening: {e}")
        return ret # ERR

    # --- Image Processing ---
    try:
        img = Image.open(last_photo_path_cache)
        img_grid = gd.dot_matrix_two_dimensional_drone(
            img=img,
            drone_height=drone_height
        )
        img_grid.save("tmp.png")
        img_new = Image.open("tmp.png")
    except FileNotFoundError:
        print(f"Error: No photo found '{last_photo_path_cache}'. Photo may be deleted.")
        return ret # ERR
    except Exception as e:
        print(f"Error during photo opening/processing: {e}")
        return ret # ERR

    # --- VLM API Call ---
    try:
        if is_init:
            # Init: System prompt + annotated image + telemetry context
            conversation.add_text_message(last_prompt_text_cache)
        elif is_warning:
            # Warning: Warning text + annotated image + telemetry context
            conversation.add_text_message(collision_warning_str)

        # Standard Step: Annotated image + telemetry context
        conversation.add_image_message(img_new)
        conversation.add_text_message(telemetry_prompt_text)

        conversation.commit_transaction(send_to_vlm=True)

        response = conversation.get_latest_message()
    except InvalidArgument as e:
        print(f"ERROR: Invalid api key or arguments: {e}")
        if is_init: conversation = None
        return ret # ERR
    except Exception as e:
        print(f"Message sending to VLM failed: {e}")
        if is_init: conversation = None
        return ret # ERR

    raw = response.text or ""

    print(raw if raw.strip() else "<empty>")

    # --- Response Parsing and Execution ---
    try:
        parsed = parse_xml_response(raw)
    except ParsingError as e:
        print("[VLM] parse error:", e)
        print("Command NOT sent")
        return ret # ERR


    # Operator confirmation and command execution
    if parsed.found:
        ret = await _confirm_send(found=True)
        if ret == 1:
            await _send_command_to_client(found=True)
        elif ret == 2:
            await collision_warning()
        elif ret == 0:
            print("Cancelled by operator.")
    else:
        move = parsed.move
        ret = await _confirm_send(move=move)
        if ret == 1:
            await _send_command_to_client(move=move)
        elif ret == 2:
            await collision_warning()
        elif ret == 0:
            print("Cancelled by operator.")
    return ret

async def chat_init():
    """
    Wrapper to initialize the chat session with the VLM.
    """
    return await send_to_vlm(is_init=True)

def chat_save(chat_id):
    """
    Serializes and saves the current chat history, prompts, and images to disk.

    Args:
        chat_id (str): The unique identifier/name for the chat directory.

    Result:
        Creates a directory 'CHATS_DIR/chat_id' containing:
        - assets/: Directory containing all images from the conversation.
        - history.json: JSON file containing the message history with references to assets.
    """
    global conversation
    global last_prompt_text_cache
    global last_photo_path_cache
    global last_telemetry_path_cache

    if conversation is None:
        print("Chat with VLM is not initialized. Use CHAT_INIT first.")
        return

    chat_dir = CHATS_DIR / chat_id
    assets_dir = chat_dir / "assets"

    assets_dir.mkdir(parents=True, exist_ok=True)

    serializable_history = []
    image_counter = 0

    history = conversation.get_conversation()
    for role, content_data in history:

        role_str = role.value if hasattr(role, "value") else str(role)

        serializable_content = {
            "role": role_str,
            "parts": []
        }

        if isinstance(content_data, str):
            serializable_content["parts"].append({
                "type": "text",
                "data": content_data
            })

        else:
            try:
                image = content_data

                if image.mode in ("RGBA", "P"):
                    image = image.convert("RGB")

                ext = "jpg"
                filename = f"image_{image_counter}.{ext}"
                file_path = assets_dir / filename

                image.save(file_path, format="JPEG", quality=95)

                relative_path = str(assets_dir.name + "/" + filename)

                serializable_content["parts"].append({
                    "type": "image",
                    "path": relative_path
                })

                image_counter += 1

            except Exception as e:
                print(f"Error processing image for message {role_str}: {e}")
                continue

        serializable_history.append(serializable_content)

    json_path = chat_dir / "history.json"
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(serializable_history, f, indent=2, ensure_ascii=False)
        print(f"Successfully saved history to {json_path}")
        print(f"Successfully saved {image_counter} images to {assets_dir}")
    except Exception as e:
        print(f"Error writing JSON file {json_path}: {e}")

def chat_retrieve(chat_id):
    """
    Reconstructs and resumes a previously saved chat session.

    Reads 'history.json', loads referenced images from the disk,
    and initializes the VLM chat with the restored history.
    """
    # TODO: FIX with new wrappers
    raise NotImplementedError
    global conversation
    global last_prompt_text_cache
    global last_photo_path_cache
    global last_telemetry_path_cache

    if conversation is None:
        print("Chat with vlm is not initialized. Use CHAT_INIT first.")
        return

    chat_dir = CHATS_DIR / chat_id
    json_path = chat_dir / "history.json"

    if not json_path.exists():
        print(f"No history file found for chat_id '{chat_id}' at {json_path}")
        return

    rebuilt_history = []

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            serializable_history = json.load(f)
    except Exception as e:
        print(f"Error reading JSON file {json_path}: {e}")
        return

    for content_data in serializable_history:
        role = content_data["role"]
        rebuilt_parts = []

        for part_data in content_data["parts"]:
            # Retrieve text data
            if part_data["type"] == "text":
                rebuilt_parts.append(part_data["data"])

            # Retrieve image data
            elif part_data["type"] == "image":
                relative_path = part_data["path"]
                image_path = chat_dir / relative_path

                if image_path.exists():
                    try:
                        # Load image into the format required by the VLM
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

    # Restart the session with the reconstructed history
    #chat_session = model.start_chat(history=rebuilt_history)
    print(f"Successfully loaded chat '{chat_id}'")

async def chat_reset(loop):
    """
    Resets the active chat session (clears memory).
    Requires user confirmation via CLI input.
    """
    global conversation
    print("Are you sure you want to reset this chat? You can use CHAT_SAVE to save it first.")
    print("Type 'yes' to reset.")

    try:
        ans = await loop.run_in_executor(None, input, "> ")
    except (EOFError, KeyboardInterrupt):
        ans = "no"

    if ans.lower() == "yes":
        conversation = None
        print("Chat deleted.")
    else:
        print("Chat not deleted.")

def prompt_generation(kind, kv):
    """
    Generates and saves a system prompt based on the specified 'kind' and parameters 'kv'.
    """
    try:
        meta = _generate_prompt(kind, kv)
        saved = _save_prompt(meta)
    except Exception as e:
        print(f"Error in _generate_prompt or _save_prompt: {e}")
        return

    print(f"[PROMPT] saved -> {saved['txt']} (+meta {saved['json']})")

# Receiving command from the user.
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
    global last_prompt_text_cache
    global last_photo_path_cache
    global last_telemetry_path_cache

    loop = asyncio.get_running_loop()
    print("Commands: PHOTO_WITH_TELEMETRY | SEND_PHOTO | TELEMETRY | PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..] | q")
    print("          CHAT_INIT | CHAT_RESET | CHAT_SAVE <name> | CHAT_RETRIEVE <name> | SEND_TO_VLM")
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
            await send_message(cmd)
            continue

        if cmd == "send_to_vlm":
            await send_to_vlm()
            continue

        if cmd.startswith("chat_init"):
            await chat_init()
            continue

        # Usage: CHAT_SAVE <name_of_the_chat>
        if cmd.startswith("chat_save "):

            cmd = cmd.split()
            if len(cmd) != 2:
                print("Usage: CHAT_SAVE <name_of_the_chat>")
                continue
            chat_id = cmd[1]

            chat_save(chat_id)

            continue

        # Usage: CHAT_RETRIEVE <chat_name>
        if cmd.startswith("chat_retrieve "):

            cmd = cmd.split()
            if len(cmd) != 2:
                print("Usage: CHAT_SAVE <name_of_the_chat>")
                continue
            chat_id = cmd[1]

            chat_retrieve(chat_id)

            continue

        if cmd.startswith("chat_reset"):
            await chat_reset(loop)
            continue

        # Usage: PROMPT <kind> object=<object> glimpses=<glimpses> area=<area>
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

            prompt_generation(kind, kv)

            continue

        else:
            print("Commands: PHOTO_WITH_TELEMETRY | SEND_PHOTO | TELEMETRY | PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..] | q")
            print("          CHAT_INIT | CHAT_DELETE | CHAT_SAVE <name> | CHAT_RETRIEVE <name> | SEND_TO_VLM")

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