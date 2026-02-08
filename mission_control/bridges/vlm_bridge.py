import asyncio
import json

from websockets.frames import CloseCode

from conversation.abstract_conversation import Role
from conversation.conversations import LLM_BACKEND_FACTORIES
from mission_control.core.config import Config
from mission_control.core.mission_context import MissionContext
from mission_control.utils.image_processing import add_grid
from mission_control.utils.parsers import parse_telemetry
from response_parsers.xml_response_parser import parse_xml_response, ParsingError


class VLMBridge:
    """ Bridge for the communication between the server and the VLM. """

    def __init__(self, config : Config, mission_context : MissionContext):
        self.config = config
        self.mission_context = mission_context
        self.collision_warning_str = "Your move would cause a collision. Make other move."

    # TODO: we need to probably raise exceptions (everywhere :(( )
    async def send_to_vlm(self, is_warning=False):
        """
        Prepares and sends the current context (image, telemetry, prompts) to the Vision Language Model.

        Args:
            is_warning (bool): If True, injects a collision warning prompt to force a corrective decision.

        Returns ActionStatus.
        """
        # --- Chat Initialization Checks ---
        if self.mission_context.conversation is None:
            print("Chat with vlm is not initialized. Use CHAT_INIT first.")
            return

        # --- Data Availability Checks ---
        if self.mission_context.last_photo_path_cache is None or self.mission_context.last_telemetry_path_cache is None:
            print("No photo or telemetry cached - it may be because no photo/telemetry was requested yet.")
            return

        # --- Telemetry Processing ---
        try:
            telemetry_data = parse_telemetry(self.mission_context.last_telemetry_path_cache)
            telemetry_prompt_text = telemetry_data[0]
            drone_height = telemetry_data[1]
        except FileNotFoundError:
            print(f"Error: No telemetry found '{self.mission_context.last_telemetry_path_cache}'. Data may be deleted.")
            return
        except Exception as e:
            print(f"Error during telemetry opening: {e}")
            return

        # --- Image Processing ---
        try:
            img_new = add_grid(self.mission_context.last_photo_path_cache, drone_height)
        except FileNotFoundError:
            print(f"Error: No photo found '{self.mission_context.last_photo_path_cache}'. Photo may be deleted.")
            return
        except Exception as e:
            print(f"Error during photo opening/processing: {e}")
            return

        # --- VLM API Call ---
        try:
            if is_warning:
                # Warning: Warning text + image with a grid + telemetry context
                self.mission_context.conversation.add_text_message(self.collision_warning_str)

            # Standard Step: image with a grid + telemetry context
            self.mission_context.conversation.add_image_message(img_new)
            self.mission_context.conversation.add_text_message(telemetry_prompt_text)

            self.mission_context.conversation.commit_transaction(send_to_vlm=True)

            # Is it blocking operation??
            response = self.mission_context.conversation.get_latest_message()
        except Exception as e:
            print(f"Message sending to VLM failed: {e}")
            return

        raw = response.text or ""

        # --- Response Parsing and Execution ---
        try:
            parsed = parse_xml_response(raw)
        except ParsingError as e:
            print("[VLM] parse error:", e)
            print("Command NOT sent")
            return

        self.mission_context.parsed_response = parsed

    async def chat_init(self):
        """"""
        if self.mission_context.conversation is not None:
            print("Chat already exists. Use CHAT_DELETE to delete the chat first.")
            return

        if self.mission_context.last_prompt_text_cache is None:
            print("No prompt generated yet. Use PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..].")
            return

        # Get the proper LLM backend using factories.
        factory = LLM_BACKEND_FACTORIES[self.config.model_backend](self.config.model_name)
        self.mission_context.conversation = factory.get_conversation()
        self.mission_context.conversation.begin_transaction(Role.USER)  # Chat initialization.
        self.mission_context.conversation.add_text_message(self.mission_context.last_prompt_text_cache)
        return

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