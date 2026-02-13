from websockets.frames import CloseCode

from mission_control.core.config import Config
from mission_control.core.exceptions import VLMConnectionError, VLMParseError, VLMPreconditionsNotMetError
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

        Raises:
            VLMConnectionError: If there is an issue with the VLM connection.
            VLMParseError: If the VLM response cannot be parsed.
            VLMPreconditionsNotMetError: If preconditions for sending data to VLM are not met.
            FileNotFoundError: If the photo or telemetry file is not found.
        """

        # All exceptions are raised up the stream.
        self._validate_preconditions()

        input_data = self._prepare_input()

        img, telemetry_text = input_data

        raw_response = self._execute_transaction(img, telemetry_text, is_warning)

        self._parse_and_store_result(raw_response)

    def _validate_preconditions(self):
        # --- Chat Initialization Checks ---
        if self.mission_context.conversation is None:
            raise VLMPreconditionsNotMetError("Chat with VLM is not initialized. Use CHAT_INIT first.")

        # --- Data Availability Checks ---
        if (self.mission_context.last_photo_path_cache is None
                or self.mission_context.last_telemetry_path_cache is None):
            raise VLMPreconditionsNotMetError("No photo or telemetry cached. Cannot send data to VLM.")

    def _prepare_input(self):
        # --- Telemetry Processing ---
        try:
            telemetry_data = parse_telemetry(self.mission_context.last_telemetry_path_cache)
            telemetry_prompt_text = telemetry_data[0]
            drone_height = telemetry_data[1]
        except FileNotFoundError as e:
            print(f"Error: No telemetry found '{self.mission_context.last_telemetry_path_cache}'. Data may be deleted.")
            raise e
        except Exception as e:
            print(f"Error during telemetry opening: {e}")
            raise

        # --- Image Processing ---
        try:
            img_new = add_grid(self.mission_context.last_photo_path_cache, drone_height)
        except FileNotFoundError as e:
            print(f"Error: No photo found '{self.mission_context.last_photo_path_cache}'. Photo may be deleted.")
            raise e
        except Exception as e:
            print(f"Error during photo opening/processing: {e}")
            raise

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
            raise VLMConnectionError(f"Message sending to VLM failed: {e}") from e

        return response.text

    def _parse_and_store_result(self, raw):
        # --- Response Parsing and Execution ---
        try:
            parsed = parse_xml_response(raw)
        except ParsingError as e:
            raise VLMParseError(f"VLM response parsing error: {e}") from e

        self.mission_context.parsed_response = parsed
