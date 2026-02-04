import asyncio, os, signal, json, time
import errno
from argparse import ArgumentError
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import websockets
from typing import Dict, Any, Callable, Awaitable
import io
from conversation.abstract_conversation import Role
from conversation.conversations import LLM_BACKEND_FACTORIES
from prompt_generation.prompts import Prompts, PROMPT_FACTORIES
from response_parsers.xml_response_parser import parse_xml_response, ParsingError
from Pillow import Image
import base64
from enum import IntEnum
import add_guardrails as gd
from websockets.frames import CloseCode

NOTE_TIMEOUT_SEC = int(os.environ.get("NOTE_TIMEOUT_SEC", "15"))

stop = asyncio.Event()

''' ------------------- ENVIRONMENTAL VARIABLES --------------------------- '''

class Config:
    def __init__(self):
        # VLM model for the LLM backend factories
        self.model_backend = os.environ.get("MODEL_BACKEND", "gemini")
        self.model_name = os.environ.get("MODEL_NAME", "gemini-2.5-flash")

        # Host and port on which to listen for the data from the drone.
        self.host = os.environ.get("WS_HOST", "0.0.0.0")
        self.port = int(os.environ.get("WS_PORT", "8080"))

        # Maximum size of the message.
        self.max_ws_mb = int(os.environ.get("MAX_WS_MB", "25"))

        # Directories to save the output.
        self.chats_dir = Path(os.environ.get("CHATS_DIR", "saved_chats"))
        self.upload_dir = os.environ.get("UPLOAD_DIR", "uploads")
        self.prompts_dir = os.environ.get("PROMPTS_DIR", "prompts")
        self.telemetry_dir = os.environ.get("TELEMETRY_DIR", "telemetry")
        os.makedirs(self.chats_dir, exist_ok=True)
        os.makedirs(self.upload_dir, exist_ok=True)
        os.makedirs(self.prompts_dir, exist_ok=True)
        os.makedirs(self.telemetry_dir, exist_ok=True)

        # here just to remember to add it to environ.
        self.gemini_api_key = os.environ.get("GEMINI_AI_KEY", None)
        self.gpt_api_key = os.environ.get("OPEN_AI_KEY", None)


collision_warning_str = "Your move would cause a collision. Make other move."


@dataclass
class MissionContext:
    # Global variable for VLM communication
    conversation = None

    # Cache of last saved photo, telemetry and prompt (for easy access)
    last_photo_path_cache = None
    last_telemetry_path_cache = None
    last_prompt_text_cache = None

class DroneBridge:
    """ Handles WebSocket communication between the server and the drone. """
    def __init__(self, config : Config, mission_context: MissionContext):
        self.client = None
        self.config = config
        self.mission_context = mission_context
        self.server = None

    async def start(self):
        """ Starts WebSocket server in the background.

            Raises error if occurs.
        """
        print(f"[WS] Starting server on {self.config.host}:{self.config.port}...")

        # Open WebSocket server.
        try:
            self.server = await websockets.serve(
                self.handler,
                self.config.host,
                self.config.port,
                max_size=self.config.max_ws_mb * 1024 * 1024
            )
            print("[WS] Server is running and listening for connections.")

        except OSError as e:
            print(f"[CRITICAL ERROR] Could not start WebSocket server on port {self.config.port}.")
            if e.errno == errno.EADDRINUSE:
                print("REASON: Port is already in use!")
                print("HINT: Check if another instance is running or wait a moment.")
            else:
                print(f"REASON: {e.strerror} (Errno: {e.errno})")

            self.server = None

            # Raise the error up the stream.
            raise e

    async def stop(self):
        """ Closes the server and disconnects connected drone. """
        print("[WS] Stopping server...")

        # Closing the server.
        if self.server:
            try:
                self.server.close()
                await self.server.wait_closed()
            except Exception as e:
                # Only log - we want to disconnect the drone.
                print(f"[WS] Warning: Error checking server close: {e}")

        # Disconnecting the drone.
        if self.client:
            try:
                await self.client.close(code=CloseCode.GOING_AWAY, reason="Server shutdown")
            except websockets.ConnectionClosed:
                # Drone might be already disconnected.
                pass
            except Exception as e:
                print(f"[WS] Warning: Error closing client connection: {e}")
            finally:
                self.client = None

        print("[WS] Server stopped.")

    async def send_message(self, cmd):
        """ Transmits a message to the connected drone via WebSocket.

        :return:
            True if successful, False otherwise.
        """
        ws = self.client
        if ws is None:
            print("No drone connected!")
            return False
        try:
            await ws.send(cmd.upper())
            print(f"[WS] {cmd.upper()} sent to the drone.")
            return True
        except Exception as e:
            print(f"[WS] send failed: {e}")
            return False

    async def send_command(self, *, found: bool = False, move=None) -> bool:
        """ Send command to the drone.

          - found=True  ->  {"type":"COMMAND","action":"FOUND", ...}
          - move=(x,y,z)->  {"type":"COMMAND","move":[x,y,z], ...}
        :return:
            True if successful, False otherwise.
        """
        ws = self.client
        if ws is None:
            print("[WS] No drone connected - command NOT sent.")
            return False

        payload : Dict[str, Any] = {
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

        # Sending payload as a JSON.
        try:
            await ws.send(json.dumps(payload))
            print("[WS] COMMAND sent to the drone.")
            return True
        except Exception as e:
            print("[WS] COMMAND send failed:", e)
            return False

    async def handler(self, ws):
        """ Handle received messages from the drone. """
        peer = ws.remote_address # IP address and port of the connected drone.
        if self.client is not None:
            print(f"[WS] REJECTED connection from {peer} (System busy)")
            await ws.send("[SERVER] ERROR: System busy. Another drone is already connected.")
            return

        self.client = ws
        print(f"[WS] connected: {peer}")

        try:
            # Wait for incoming messages.
            async for message in ws:
                # binary photo - 'photo' command sends photo from rpi that way (idk why, probably will change)
                if isinstance(message, (bytes, bytearray)):
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    file_base = f"img_{ts}"
                    file_name = f"{file_base}.jpg"
                    path = os.path.join(self.config.upload_dir, file_name)
                    with open(path, "wb") as f:
                        f.write(message)
                    print(f"[WS] saved binary -> {path}")
                    await ws.send(f"[SERVER] SAVED {path}")
                    continue

                # If the message is not a photo, try to decode it as JSON.
                # Clean whitespace prefixes/suffixes.
                text = message.strip()
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    print(f"[WS] Ignored message (not JSON nor binary): {text}")
                    continue

                if not isinstance(obj, dict):
                    print(f"[WS] Ignored non-dict JSON: {obj}")
                    continue

                # TODO: is it useful?
                if obj.get("type") == "ACK" and obj.get("of") == "COMMAND":
                    print(f"[ACK ← RPi] COMMAND seq={obj.get('seq')} ok={obj.get('ok')} err={obj.get('error')}")
                    continue

                # Get the telemetry from the drone. Data is NOT saved in cache, and so not used when talking with vlm.
                if obj.get("type") == "TELEMETRY":
                    data = obj.get("data", {})
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    file_base = f"telemetry_{ts}"
                    file_name = f"{file_base}.json"
                    path = os.path.join(self.config.telemetry_dir, file_name)

                    payload = {
                        "received_at": ts,
                        "associated_photo": None,
                        "data": data
                    }

                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False, indent=2)
                    print(f"[WS] saved telemetry(JSON) -> {path}")
                    await ws.send(f"[SERVER] SAVED {path}")
                    continue

                # Get the data (telemetry and photo) from the drone.
                if obj.get("type") == "PHOTO_WITH_TELEMETRY":

                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

                    photo_base64 = obj.get("photo")
                    telemetry_data = obj.get("telemetry", {})

                    # Photo
                    if not photo_base64:
                        print("[WS] 'PHOTO_WITH_TELEMETRY' received but 'photo' field is missing.")
                        await ws.send("[SERVER] ERROR: Photo data missing in combined message.")
                        continue

                    try:
                        photo_data = base64.b64decode(photo_base64)
                    except TypeError as e:
                        print(f"[WS] Failed to decode Base64 photo data: {e}")
                        await ws.send("[SERVER] ERROR: Invalid photo data encoding.")
                        continue

                    img_file_base = f"img_{ts}"
                    img_file_name = f"{img_file_base}.jpg"
                    img_path = os.path.join(self.config.upload_dir, img_file_name)

                    try:
                        img_cropped, side = crop_img_square(photo_data)

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
                    tel_path = os.path.join(self.config.telemetry_dir, tel_file_name)

                    # We are caching paths for easier access after when sending to VLM.
                    self.mission_context.last_photo_path_cache = img_path
                    self.mission_context.last_telemetry_path_cache = tel_path

                    payload = {
                        "received_at": ts,
                        "associated_photo": img_file_name,
                        "data": telemetry_data
                    }

                    with open(tel_path, "w", encoding="utf-8") as f:
                        try:
                            json.dump(payload, f, ensure_ascii=False, indent=2)
                            print(f"[WS] saved telemetry -> {tel_path}")
                        except Exception as e:
                            print(f"[WS] error saving telemetry: {e}")

                    await ws.send("[SERVER] Photo and telemetry received.")
                    continue

                continue

        except websockets.ConnectionClosed:
            print(f"[WS] disconnected: {peer}.")

        except Exception as e:
            print(f"[WS] error: {e}.")

        finally:
            # Always reset the client.
            self.client = None

class VLMBridge:
    """ Bridge for the communication between the server and the drone. """

    def __init__(self, config : Config, mission_context : MissionContext, drone : DroneBridge):
        self.config = config
        self.mission_context = mission_context
        self.drone = drone

    async def _confirm_send(self, move=None, found=False):
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

    async def send_to_vlm(self, is_init=False, is_warning=False):
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
        # --- Chat Initialization Checks ---
        if is_init:
            if self.mission_context.conversation is not None:
                print("Chat already exists. Use CHAT_DELETE to delete the chat first.")
                return ActionStatus.ERROR

            if self.mission_context.last_prompt_text_cache is None:
                print("No prompt generated yet. Use PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..].")
                return ActionStatus.ERROR

            factory = LLM_BACKEND_FACTORIES[self.config.model_backend](self.config.model_name)
            conversation = factory.get_conversation()
            conversation.begin_transaction(Role.USER)
        else:
            if self.mission_context.conversation is None:
                print("Chat with vlm is not initialized. Use CHAT_INIT first.")
                return ActionStatus.ERROR

        # --- Data Availability Checks ---
        if self.mission_context.last_photo_path_cache is None or self.mission_context.last_telemetry_path_cache is None:
            print("No photo or telemetry cached - it may be because no photo/telemetry was requested yet.")
            return ActionStatus.ERROR

        # --- Telemetry Processing ---
        try:
            telemetry_data = parse_telemetry(self.mission_context.last_telemetry_path_cache)
            telemetry_prompt_text = telemetry_data[0]
            drone_height = telemetry_data[1]
        except FileNotFoundError:
            print(f"Error: No telemetry found '{self.mission_context.last_telemetry_path_cache}'. Data may be deleted.")
            return ActionStatus.ERROR
        except Exception as e:
            print(f"Error during telemetry opening: {e}")
            return ActionStatus.ERROR

        # --- Image Processing ---
        try:
            img_new = add_grid(self.mission_context.last_photo_path_cache, drone_height)
        except FileNotFoundError:
            print(f"Error: No photo found '{self.mission_context.last_photo_path_cache}'. Photo may be deleted.")
            return ActionStatus.ERROR
        except Exception as e:
            print(f"Error during photo opening/processing: {e}")
            return ActionStatus.ERROR

        # --- VLM API Call ---
        try:
            if is_init:
                # Init: System prompt + annotated image + telemetry context
                self.mission_context.conversation.add_text_message(self.mission_context.last_prompt_text_cache)
            elif is_warning:
                # Warning: Warning text + annotated image + telemetry context
                self.mission_context.conversation.add_text_message(collision_warning_str)

            # Standard Step: Annotated image + telemetry context
            self.mission_context.conversation.add_image_message(img_new)
            self.mission_context.conversation.add_text_message(telemetry_prompt_text)

            self.mission_context.conversation.commit_transaction(send_to_vlm=True)

            response = self.mission_context.conversation.get_latest_message()
        except Exception as e:
            print(f"Message sending to VLM failed: {e}")
            if is_init: self.mission_context.conversation = None
            return ActionStatus.ERROR

        raw = response.text or ""

        print(raw if raw.strip() else "<empty>")

        # --- Response Parsing and Execution ---
        try:
            parsed = parse_xml_response(raw)
        except ParsingError as e:
            print("[VLM] parse error:", e)
            print("Command NOT sent")
            return ActionStatus.ERROR

        # Operator confirmation and command execution
        if parsed.found:
            raw_status = await self._confirm_send(found=True)
        else:
            move = parsed.move
            raw_status = await self._confirm_send(move=move)

        # Konwersja inta na Enum (zakładając, że _confirm_send zwraca int)
        try:
            ret = ActionStatus(raw_status)
        except ValueError:
            print(f"Unknown status received: {raw_status}")
            ret = ActionStatus.ERROR

        # Obsługa logiki na podstawie nazwanych stanów
        if ret == ActionStatus.CONFIRMED:
            # Jeśli parsed.found jest True, nie mamy zmiennej 'move', więc musimy obsłużyć to warunkowo
            if parsed.found:
                await self.drone.send_command(found=True)
            else:
                await self.drone.send_command(move=parsed.move)

        elif ret == ActionStatus.WARNING:
            await self.collision_warning()

        elif ret == ActionStatus.CANCELLED:
            print("Cancelled by operator.")
        return ret

    async def collision_warning(self):
        """
        Triggers a collision warning context update to the VLM.
        Used when the operator deems a move risky.
        """
        await self.send_to_vlm(is_warning=True)

    async def chat_init(self):
        """ Wrapper to initialize the chat session with the VLM."""
        return await self.send_to_vlm(is_init=True)

    async def chat_save(self, chat_id):
        """ Serializes and saves the current chat history - prompts and images to disk.

        :param chat_id: The unique identifier/name for the chat directory.

        Result:
            Creates a directory 'CHATS_DIR/chat_id' containing:
            - assets/: Directory containing all images from the conversation.
            - history.json: JSON file containing the message history with references to assets.
        """

        if self.mission_context.conversation is None:
            print("Chat with VLM is not initialized. Use CHAT_INIT first.")
            return

        chat_dir = self.config.chats_dir / chat_id
        assets_dir = chat_dir / "assets"

        assets_dir.mkdir(parents=True, exist_ok=True)

        serializable_history = []
        image_counter = 0

        history = self.mission_context.conversation.get_conversation()
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

    async def chat_retrieve(self, chat_id):
        """
        Reconstructs and resumes a previously saved chat session.

        Reads 'history.json', loads referenced images from the disk,
        and initializes the VLM chat with the restored history.
        """
        # TODO: FIX with new wrappers
        raise NotImplementedError


        if self.mission_context.conversation is None:
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
        # chat_session = model.start_chat(history=rebuilt_history)
        print(f"Successfully loaded chat '{chat_id}'")

    async def chat_reset(self):
        """ Resets the active chat session (clears memory).

        Requires user confirmation via CLI input.
        """

        loop = asyncio.get_event_loop()

        print("Are you sure you want to reset this chat? You can use CHAT_SAVE to save it first.")
        print("Type 'yes' to reset.")

        try:
            ans = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            ans = "no"

        if ans.lower() == "yes":
            self.mission_context.conversation = None
            print("Chat deleted.")
        else:
            print("Chat not deleted.")


class ActionStatus(IntEnum):
    ERROR = -1
    CANCELLED = 0
    CONFIRMED = 1
    WARNING = 2


class MissionControl:
    def __init__(self):
        self.config = Config()
        self.mission_context = MissionContext()

        self.prompt_manager = PromptManager()

        self.drone = DroneBridge(self.config, self.mission_context)
        self.vlm = VLMBridge(self.config, self.mission_context, self.drone)

        # Dispatcher
        self.commands : Dict[str, Callable[[str, str], Awaitable[None]]] = {
            "search": self._handle_search,

            "send_photo": lambda cmd, _: self.drone.send_message(cmd),
            "telemetry": lambda cmd, _: self.drone.send_message(cmd),
            "photo_with_telemetry": lambda cmd, _: self.drone.send_message(cmd),

            "send_to_vlm": lambda c, a: self.vlm.send_to_vlm(),

            "chat_init": lambda c, a: self.vlm.chat_init(),
            "chat_save": lambda _, args: self.vlm.chat_save(args),
            "chat_retrieve": lambda _, args: self.vlm.chat_retrieve(args),
            "chat_reset": lambda c, a: self.vlm.chat_reset(),

            "prompt": self._handle_prompt_cmd
        }

    async def _handle_search(self, cmd, args):
        """ Handle search command. """
        kind, kv = parse_prompt_arguments(args)
        await self.search(kind, kv)

    async def _handle_prompt_cmd(self, cmd, args):
        kind, kv = parse_prompt_arguments(args)
        self.prompt_manager.generate_and_save(kind, kv)

    async def search(self, kind, kv):
        """ Orchestrates an automated search test sequence.

        This function handles the end-to-end flow: generating the initial prompt,
        sending commands to the drone, initializing the VLM chat, and entering
        a loop to process visual feedback until the 'glimpses' limit is reached
        or the object is found or the test is aborted.

        The user is expected to validate the VLM's decisions during the process
        (accept, report collision, or stop).
        """
        print("\n--- SEARCHING... ---")
        self.prompt_manager.generate_and_save(kind, kv)
        await self.drone.send_message("photo_with_telemetry")
        ret = await self.vlm.chat_init()
        await self.vlm.chat_save("autosave")
        count = 1
        while ret not in {0, 3} and count != kv["glimpses"]:
            await self.drone.send_message("photo_with_telemetry")
            ret = await self.vlm.send_to_vlm()
            await self.vlm.chat_save("autosave")
            count += 1

    async def stdin_repl(self):
        """ Handling commands received from the user.

            Parses input and forwards it to the proper method.
        """


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

        print_help()


        while not stop.is_set():
            try:
                # TODO: fix
                line = await loop.run_in_executor(None, input, "> ")
            except (EOFError, KeyboardInterrupt):
                line = "q"
            line = (line or " ").strip()
            if not line:
                continue

            # Unify input
            cmd = line.lower()

            #Split commands from arguments.
            parts = cmd.split(" ", 1)
            command = parts[0]
            args = parts[1] if len(parts) > 1 else ""

            # Close the server.
            if command in ("q", "quit", "exit"):
                _signal_handler()
                break

            # Take and use the method from those defined in __init__
            handler = self.commands.get(command)

            if handler:
                try:
                    await handler(command, args)
                except ArgumentError:
                    print_help()
                except Exception as e:
                    print(f"[ERROR] Command failed: {e}")
            else:
                print_help()

    async def run(self):
        """ Main function for async loop. """
        loop = asyncio.get_running_loop()

        # Instead of closing, use _signal_handler function
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        # Start the WebSocket connection.
        try:
            await self.drone.start()
        except OSError:
            print("[CRITICAL] Failed to start drone bridge. Exiting.")
            return

        # Start those two method concurrently.
        repl_task = asyncio.create_task(self.stdin_repl()) # CLI
        stop_task = asyncio.create_task(stop.wait()) # signal handler

        # Wait for the first one to complete.
        done, pending = await asyncio.wait(
            [repl_task, stop_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        # Cancel those which haven't completed yet.
        for task in pending:
            task.cancel()
            try:
                await task  # Wait for the confirmation
            except asyncio.CancelledError:
                pass

        # Stop the WebSocket connection.
        await self.drone.stop()


class PromptManager:
    def __init__(self, config : Config, mission_context : MissionContext):
        self.config = config
        self.mission_context = mission_context

    def _generate_prompt(self, kind: str, kv: Dict[str, str]) -> Dict[str, str]:
        """
        kind: 'FS-1' lub 'FS-2'
        kv:   dict with parameters (object, glimpses, area)
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

    def _save_prompt(self, prompt_meta: Dict[str, str]) -> Dict[str, str]:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{prompt_meta['kind'].lower()}_{ts}"
        txt_path = os.path.join(self.config.prompts_dir, base + ".txt")

        self.mission_context.last_prompt_text_cache = prompt_meta["text"]

        json_path = os.path.join(self.config.prompts_dir, base + ".json")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(prompt_meta["text"])
        meta_to_save = dict(prompt_meta)
        meta_to_save.pop("text", None)
        meta_to_save["saved_at"] = ts
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(meta_to_save, f, ensure_ascii=False, indent=2)
        return {"txt": txt_path, "json": json_path}


    def generate_and_save(self, kind, kv):
        """ Generates and saves a system prompt based on the specified 'kind' and parameters 'kv'. """
        try:
            meta = self._generate_prompt(kind, kv)
            saved = self._save_prompt(meta)
        except Exception as e:
            print(f"Error in _generate_prompt or _save_prompt: {e}")
            return

        print(f"[PROMPT] saved -> {saved['txt']} (+meta {saved['json']})")


# Parse telemetry from json to the prompt, that will be added to the photo.
def parse_telemetry(path):
    with open(path, "r", encoding="utf-8") as f:
        telemetry = json.load(f)

    telemetry_data = telemetry.get("data", {})
    height = telemetry_data.get("position", {}).get("alt", 10)
    return [f"Your current altitude is {height} meters above ground level.", height]

''' --------------------------- CLIENT SERVICES -------------------------- '''

def parse_prompt_arguments(cmd):
    parts = cmd.split()
    if len(parts) < 1:
        print("Usage: PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..]")
        raise ArgumentError
    kind = parts[0].upper()
    if kind not in ("FS-1", "FS-2"):
        print("Kind must be FS-1 or FS-2")
        raise ArgumentError

    kv: Dict[str, str] = {}
    for token in parts[1:]:
        if "=" in token:
            k, v = token.split("=", 1)
            kv[k.strip().lower()] = v.strip()
    return kind, kv

def print_help():
    print(
        "Commands: PHOTO_WITH_TELEMETRY | SEND_PHOTO | TELEMETRY | PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..] | q")
    print("          CHAT_INIT | CHAT_RESET | CHAT_SAVE <name> | CHAT_RETRIEVE <name> | SEND_TO_VLM")

def crop_img_square(photo_data):
    img = Image.open(io.BytesIO(photo_data))
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    right = left + side
    bottom = top + side

    return img.crop((left, top, right, bottom)), side

def add_grid(photo_path, drone_height):
    img = Image.open(photo_path)
    img_grid = gd.dot_matrix_two_dimensional_drone(
        img=img,
        drone_height=drone_height
    )
    # It might seem redundant, but without it while sending
    # original photo from the file is taken (Python optimization)
    img_grid.save("tmp.png")
    return Image.open("tmp.png")

def _signal_handler():
    if not stop.is_set():
        print("\n[WS] shutdown requested (signal). Closing clients…")
        stop.set()


if __name__ == "__main__":
    mission = MissionControl()
    try:
        asyncio.run(mission.run())
    except KeyboardInterrupt:
        pass