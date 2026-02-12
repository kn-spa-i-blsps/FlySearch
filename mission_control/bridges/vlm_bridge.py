from websockets.frames import CloseCode

from mission_control.core.config import Config
from mission_control.core.mission_context import MissionContext
from mission_control.utils.image_processing import add_grid
from mission_control.utils.parsers import parse_telemetry, parse_xml_response, ParsingError


class VLMBridge:
    """ Bridge for the communication between the server and the VLM. """

    def __init__(self, config : Config, mission_context : MissionContext):
        self.config = config
        self.mission_context = mission_context
        self.collision_warning_str = "Your move would cause a collision. Make other move."

    async def send_to_vlm(self, is_warning=False):
        """
        Prepares and sends the current context (image, telemetry, prompts) to the Vision Language Model.

        Args:
            is_warning (bool): If True, injects a collision warning prompt to force a corrective decision.

        Returns ActionStatus.
        """
        if not self._validate_preconditions():
            return

        input_data = self._prepare_input()
        if input_data is None:
            return

        img, telemetry_text = input_data

        raw_response = self._execute_transaction(img, telemetry_text, is_warning)
        if raw_response is None:
            return

        self._parse_and_store_result(raw_response)

    def _validate_preconditions(self):
        # --- Chat Initialization Checks ---
        if self.mission_context.conversation is None:
            print("Chat with vlm is not initialized. Use CHAT_INIT first.")
            return False

        # --- Data Availability Checks ---
        if (self.mission_context.last_photo_path_cache is None
                or self.mission_context.last_telemetry_path_cache is None):
            print("No photo or telemetry cached - it may be because no photo/telemetry was requested yet.")
            return False

        return True

    def _prepare_input(self):
        # --- Telemetry Processing ---
        try:
            telemetry_data = parse_telemetry(self.mission_context.last_telemetry_path_cache)
            telemetry_prompt_text = telemetry_data[0]
            drone_height = telemetry_data[1]
        except FileNotFoundError:
            print(f"Error: No telemetry found '{self.mission_context.last_telemetry_path_cache}'. Data may be deleted.")
            return None
        except Exception as e:
            print(f"Error during telemetry opening: {e}")
            return None

        # --- Image Processing ---
        try:
            img_new = add_grid(self.mission_context.last_photo_path_cache, drone_height)
        except FileNotFoundError:
            print(f"Error: No photo found '{self.mission_context.last_photo_path_cache}'. Photo may be deleted.")
            return None
        except Exception as e:
            print(f"Error during photo opening/processing: {e}")
            return None

        return img_new, telemetry_prompt_text

    def _execute_transaction(self, img, telemetry_text, is_warning):
        # --- Add messages ---
        try:
            if is_warning:
                # Warning: Warning text + image with a grid + telemetry context
                self.mission_context.conversation.add_text_message(self.collision_warning_str)

            # Standard Step: image with a grid + telemetry context
            self.mission_context.conversation.add_image_message(img)
            self.mission_context.conversation.add_text_message(telemetry_text)

            # Send message
            self.mission_context.conversation.commit_transaction(send_to_vlm=True)

            # Is it blocking operation??
            response = self.mission_context.conversation.get_latest_message()
        except Exception as e:
            print(f"Message sending to VLM failed: {e}")
            return None

        return response.text

    def _parse_and_store_result(self, raw):
        # --- Response Parsing and Execution ---
        try:
            parsed = parse_xml_response(raw)
        except ParsingError as e:
            print("[VLM] parse error:", e)
            print("Command NOT sent")
            return

        self.mission_context.parsed_response = parsed