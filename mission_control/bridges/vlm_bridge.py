import asyncio
import time

from conversation.abstract_conversation import Role
from mission_control.core.mission_context import MissionContext

from mission_control.core.config import Config
from mission_control.core.exceptions import (
    VLMConnectionError,
    VLMParseError,
    VLMPreconditionsNotMetError,
)
from mission_control.utils.image_processing import add_grid
from mission_control.utils.parsers import (
    ParsingError,
    parse_telemetry,
    parse_xml_response,
)


class VLMBridge:
    """Bridge for the communication between the server and the VLM."""

    PING_PROMPT = 'Say exactly "hello FlySearch".'
    DEFAULT_PING_TIMEOUT_SECONDS = 120.0

    def __init__(self, config: Config, mission_context: MissionContext):
        self.config = config
        self.mission_context = mission_context
        self.collision_warning_str = (
            "Your move would cause a collision. Make other move."
        )

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

    async def ping_vlm(self, prompt: str = "") -> bool:
        """Send an isolated health-check request to the configured VLM.

        The probe deliberately creates a short-lived conversation rather than
        using ``mission_context.conversation``.  A diagnostic command must not
        add its prompt/answer to the mission chat or require a chat, image, or
        telemetry to have been initialized first.

        Returns:
            ``True`` when the VLM returns non-empty text, otherwise ``False``.
            All expected failure details are printed here so the CLI can remain
            useful without exposing a traceback for normal communication faults.
        """
        ping_prompt = prompt.strip() or self.PING_PROMPT
        is_default_prompt = ping_prompt == self.PING_PROMPT
        backend = getattr(self.config, "model_backend", "unknown")
        model = getattr(self.config, "model_name", "unknown")
        timeout = self._ping_timeout_seconds()
        started = time.perf_counter()

        print(
            f"[VLM PING] Sending health check to backend={backend!s}, "
            f"model={model!s} (timeout={timeout:.1f}s)."
        )

        try:
            # The supported VLM clients are synchronous.  Running the entire
            # temporary transaction in a worker preserves the CLI event loop
            # and makes wait_for enforce a useful timeout.
            response = await asyncio.wait_for(
                asyncio.to_thread(self._send_ping_request, ping_prompt),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - started
            print(
                f"[VLM PING] COMMUNICATION ERROR after {elapsed:.2f}s: no response "
                f"within the {timeout:.1f}s timeout. The provider request may still "
                "be finishing in the background."
            )
            return False
        except Exception as error:
            elapsed = time.perf_counter() - started
            print(
                f"[VLM PING] COMMUNICATION ERROR after {elapsed:.2f}s "
                f"(backend={backend!s}, model={model!s}): "
                f"{self._format_ping_error(error)}"
            )
            return False

        elapsed = time.perf_counter() - started
        response = response.strip()
        if not response or response.lower() in ("none", "null"):
            print(
                f"[VLM PING] COMMUNICATION ERROR after {elapsed:.2f}s: the VLM "
                "returned an empty response. Check provider safety settings, model "
                "availability, and server logs."
            )
            return False

        compact_response = " ".join(response.split())
        if (
            is_default_prompt
            and compact_response.lower().strip(" .!\"'") == "hello flysearch"
        ):
            print(
                f"[VLM PING] OK: response received in {elapsed:.2f}s: {compact_response!r}"
            )
        elif is_default_prompt:
            print(
                f"[VLM PING] OK: response received in {elapsed:.2f}s, but it did not "
                f"match the expected greeting: {compact_response[:200]!r}"
            )
        else:
            print(
                f"[VLM PING] OK: response received in {elapsed:.2f}s: "
                f"{compact_response[:200]!r}"
            )
        return True

    def _send_ping_request(self, prompt: str) -> str:
        """Perform the synchronous, one-message ping transaction."""
        # Keep this import local: normal VLM bridge tests and deployments that
        # only use a different backend should not need every backend SDK merely
        # to import this module.
        from conversation.conversations import LLM_BACKEND_FACTORIES

        factory_class = LLM_BACKEND_FACTORIES[self.config.model_backend]
        conversation = factory_class(self.config.model_name).get_conversation()
        # OpenAIConversation normally echoes the full model response to stdout.
        # This probe supplies its own compact result line, so suppress that
        # backend-specific echo for this temporary conversation only.
        conversation.suppress_response_output = True

        try:
            conversation.begin_transaction(Role.USER)
            conversation.add_text_message(prompt)
            conversation.commit_transaction(send_to_vlm=True)
            return self._response_to_text(conversation.get_latest_message())
        except Exception:
            # Roll back only an unfinished temporary transaction.  A rollback
            # failure is secondary to the useful provider error we will report.
            try:
                conversation.rollback_transaction()
            except Exception:
                pass
            raise

    @staticmethod
    def _response_to_text(response) -> str:
        """Normalize the response shapes returned by the conversation backends."""
        if isinstance(response, tuple) and len(response) >= 2:
            return str(response[1])
        if isinstance(response, str):
            return response

        text = getattr(response, "text", None)
        if text is not None:
            return str(text)

        raise VLMConnectionError(
            "Unexpected response type from conversation.get_latest_message(): "
            f"{type(response).__name__}"
        )

    def _ping_timeout_seconds(self) -> float:
        configured_timeout = getattr(self.config, "vlm_ping_timeout_seconds", None)
        if isinstance(configured_timeout, (int, float)) and configured_timeout > 0:
            return float(configured_timeout)
        return self.DEFAULT_PING_TIMEOUT_SECONDS

    @staticmethod
    def _format_ping_error(error: Exception) -> str:
        """Give operators actionable, but concise, failure diagnostics."""
        error_type = type(error).__name__
        detail = str(error).strip() or "no error detail supplied"
        lower_detail = detail.lower()

        if any(
            term in lower_detail
            for term in (
                "api key",
                "api_key",
                "authentication",
                "unauthorized",
                "forbidden",
            )
        ):
            category = "authentication/authorization"
        elif any(
            term in lower_detail for term in ("rate limit", "too many requests", "429")
        ):
            category = "rate limit/quota"
        elif any(
            term in lower_detail
            for term in (
                "dns",
                "name resolution",
                "connection",
                "network",
                "socket",
                "host",
            )
        ):
            category = "network/endpoint"
        elif any(term in lower_detail for term in ("not found", "404", "model")):
            category = "model/configuration"
        else:
            category = "provider/client"

        # Keep the one-line CLI response readable and avoid accidentally
        # printing an unusually long provider payload.
        compact_detail = " ".join(detail.split())[:500]
        return f"{category}; {error_type}: {compact_detail}"

    def _validate_preconditions(self):
        # --- Chat Initialization Checks ---
        if self.mission_context.conversation is None:
            raise VLMPreconditionsNotMetError(
                "Chat with VLM is not initialized. Use CHAT_INIT first."
            )

        # --- Data Availability Checks ---
        if (
            self.mission_context.last_photo_path_cache is None
            or self.mission_context.last_telemetry_path_cache is None
        ):
            raise VLMPreconditionsNotMetError(
                "No photo or telemetry cached. Cannot send data to VLM."
            )

    def _prepare_input(self):
        # --- Telemetry Processing ---
        try:
            telemetry_data = parse_telemetry(
                self.mission_context.last_telemetry_path_cache
            )
            telemetry_prompt_text = telemetry_data[0]
            drone_height = telemetry_data[1]
        except FileNotFoundError as e:
            print(
                f"Error: No telemetry found '{self.mission_context.last_telemetry_path_cache}'. Data may be deleted."
            )
            raise e
        except Exception as e:
            print(f"Error during telemetry opening: {e}")
            raise

        # --- Image Processing ---
        try:
            img_new = add_grid(
                self.mission_context.last_photo_path_cache,
                drone_height,
                camera_fov_degrees=self.config.fov_degrees,
            )
        except FileNotFoundError as e:
            print(
                f"Error: No photo found '{self.mission_context.last_photo_path_cache}'. Photo may be deleted."
            )
            raise e
        except Exception as e:
            print(f"Error during photo opening/processing: {e}")
            raise

        return img_new, telemetry_prompt_text

    def _execute_transaction(self, img, telemetry_text, is_warning):
        # --- Add messages ---
        try:
            try:
                self.mission_context.conversation.begin_transaction(Role.USER)
            except Exception as begin_error:
                if "Transaction already started" not in str(begin_error):
                    raise

            if is_warning:
                # Warning: Warning text + image with a grid + telemetry context
                self.mission_context.conversation.add_text_message(
                    self.collision_warning_str
                )

            # Standard Step: image with a grid + telemetry context
            self.mission_context.conversation.add_image_message(img)
            self.mission_context.conversation.add_text_message(telemetry_text)

            # Send message
            self.mission_context.conversation.commit_transaction(send_to_vlm=True)

            # Is it blocking operation??
            response = self.mission_context.conversation.get_latest_message()
        except Exception as e:
            try:
                self.mission_context.conversation.rollback_transaction()
            except Exception:
                pass
            raise VLMConnectionError(f"Message sending to VLM failed: {e}") from e

        if isinstance(response, tuple) and len(response) >= 2:
            return str(response[1])

        if isinstance(response, str):
            return response

        text = getattr(response, "text", None)
        if text is not None:
            return str(text)

        raise VLMConnectionError(
            f"Unexpected response type from conversation.get_latest_message(): {type(response).__name__}"
        )

    def _parse_and_store_result(self, raw):
        # --- Response Parsing and Execution ---
        try:
            parsed = parse_xml_response(raw)
        except ParsingError as e:
            raise VLMParseError(f"VLM response parsing error: {e}") from e

        self.mission_context.parsed_response = parsed
