import asyncio
import logging
import signal
import sys

from mission_control.bridges.drone_bridge import DroneBridge
from mission_control.bridges.vlm_bridge import VLMBridge
from mission_control.core.action_status import ActionStatus
from mission_control.core.config import Config
from mission_control.core.exceptions import DroneError, VLMError, ChatError, DroneConnectionLostError, ServerError
from mission_control.core.mission_context import MissionContext
from mission_control.managers.chat_manager import ChatSessionManager
from mission_control.managers.prompt_manager import PromptManager
from mission_control.ui.cli_handler import CLIHandler
from mission_control.ui.web_server import WebServer
from mission_control.utils.parsers import parse_prompt_arguments, parse_search_arguments

DEBUG = False

log_level = logging.DEBUG if DEBUG else logging.INFO

# TODO: >= warnings should go to stderr
logging.basicConfig(
    level=log_level,
    stream=sys.stdout,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class MissionControl:
    def __init__(self):
        self.logger = logging.getLogger(__name__)   # Logger.
        self.config = Config()                      # Configuration variables - dirs, ports, hosts...
        self.mission_context = MissionContext()     # All connected modules returns info there.
        self.prompt_manager = PromptManager(        # e.g. prompt generating.
            self.config,
            self.mission_context
        )
        self.drone = DroneBridge(                   # Server <-> drone communication.
            self.config,
            self.mission_context
        )
        self.vlm = VLMBridge(                       # Server <-> VLM communication.
            self.config,
            self.mission_context,
        )
        self.chat_manager = ChatSessionManager(     # Chat management - saving etc.
            self.config,
            self.mission_context
        )

        # Dispatcher - maps command name with proper function/method.
        commands = {
            "clear_search":     lambda _, __: self.clear_search(),
            "search":           lambda _, args: self._handle_search(args),
            "chat_init":        lambda _, __: self.chat_manager.create_new_session(),
            "chat_save":        lambda _, args: self.chat_manager.save_session(args),
            "chat_retrieve":    lambda _, args: self.chat_manager.restore_session(args),
            "chat_reset":       lambda _, __: self._handle_chat_reset(),
            "prompt":           lambda _, args: self._handle_prompt_cmd(args),
            "start_recording":  lambda cmd, _: self.drone.send_recording_command(cmd),
            "stop_recording":   lambda cmd, _: self.drone.send_recording_command(cmd),
            "get_recordings":   lambda _, __: self._handle_get_recordings(),
            "pull_recordings":  lambda _, args: self._handle_pull_recordings(args),
            "send_to_vlm":      lambda c, a: self.vlm.send_to_vlm(),
            "q":                lambda c, a: self._signal_handler_wrapper(),
            "quit":             lambda c, a: self._signal_handler_wrapper(),
            "exit":             lambda c, a: self._signal_handler_wrapper(),
            "move":             lambda _, __: self.drone.send_move(
                                    found=self.mission_context.parsed_response.found,
                                    move=self.mission_context.parsed_response.move
                                ),
            "add_warning":      lambda c, a: self.vlm.send_to_vlm(
                                    is_warning=True
                                ),
            "photo_with_telemetry": lambda cmd, _: self.drone.send_message(cmd),
        }
        self.cli_handler = CLIHandler(self.mission_context, commands)  # CLI
        self.web_server = WebServer(self.mission_context)  # GUI

    ''' -------------- ASYNC LOOP METHOD -------------- '''
    async def run(self):
        """ Main function for async loop. """
        loop = asyncio.get_running_loop()

        self.mission_context.photo_received_event = asyncio.Event()

        # Instead of closing, use _signal_handler function
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._signal_handler)
            except NotImplementedError:
                pass

        # Start the connection with the drone and listen for drones.
        try:
            await self.drone.start()
        except ServerError as e:
            self.logger.error("[MC] Error on server startup. Exiting.", exc_info=True)
            # TODO: proper return.
            return

        # CLI
        repl_task = asyncio.create_task(self.cli_handler.serve())
        # WEB GUI
        web_task = asyncio.create_task(self.web_server.serve())
        # Stop signal
        stop_task = asyncio.create_task(self.mission_context.stop.wait())

        done, pending = await asyncio.wait(
            [repl_task, web_task, stop_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        self.web_server.request_stop()

        # Cancel those which haven't completed yet.
        for task in pending:
            task.cancel()
            try:
                await task  # Wait for the confirmation.
            except asyncio.CancelledError:
                pass

        # Stop the WebSocket connection.
        await self.drone.stop()

    ''' -------------- WHOLE SEARCH SEQUENCE -------------- '''
    async def search(self, name, kind, kv):
        """ Orchestrates an automated search test sequence.

        This function handles the end-to-end flow: generating the initial prompt,
        sending commands to the drone, initializing the VLM chat, and entering
        a loop to process visual feedback until the 'glimpses' limit is reached
        or the object is found or the test is aborted.

        The user is expected to validate the VLM's decisions during the process
        (accept, report collision, or stop).
        """
        self.logger.info("[SEARCH] Starting search...")
        ending_status = "Search process ended."
        moves_performed = 0
        move_limit = 0
        search_started_recording = False
        try:

            await self.web_server.broadcast_state(custom_status="Search in progress... Initializing VLM.")

            # Initial prompt.
            await self.drone.send_recording_command("start_recording")
            search_started_recording = True
            self.prompt_manager.generate_and_save(kind, kv)
            moves_performed = await self._initialize_or_restore_search_session(name)

            ret = ActionStatus.CONFIRMED
            move_limit = int(kv["glimpses"])

            while (ret in [ActionStatus.CONFIRMED, ActionStatus.WARNING]
                   and moves_performed < move_limit):

                if not self.mission_context.parsed_response:
                    # Request photo and telemetry.
                    self.mission_context.photo_received_event.clear()
                    await self.drone.send_message("photo_with_telemetry")

                    try:
                        await asyncio.wait_for(self.mission_context.photo_received_event.wait(), timeout=15.0)
                    except asyncio.TimeoutError:
                        self.logger.error("[SEARCH] Timeout: Photo not received. Aborting search.")
                        return

                    # Send it to vlm.
                    await self.vlm.send_to_vlm(is_warning=(ret == ActionStatus.WARNING))

                    # Autosave the chat.
                    await self.chat_manager.save_session(name)

                # Take parsed response and ask for confirmation.
                parsed = self.mission_context.parsed_response

                ret = await self._confirm_send(found=parsed.found, move=parsed.move)

                if ret == ActionStatus.CONFIRMED:
                    # If confirmed, send the move to the drone.
                    await self.drone.send_move(found=parsed.found, move=parsed.move)
                    moves_performed += 1
                    self.mission_context.parsed_response = None
                elif ret == ActionStatus.FOUND:
                    # If found, print the message and end the loop.
                    await self.web_server.broadcast_state(custom_status="FOUND.")
                    self.logger.info("[SEARCH] FOUND")

        except DroneConnectionLostError as e:
            self.logger.warning(f"[SEARCH] Connection lost.")
            ending_status = "Connection lost."
        except (DroneError, VLMError, ChatError) as e:
            self.logger.error(f"[SEARCH] An error occurred. Aborting search.", exc_info=True)
            ending_status = e
        except Exception as e:
            self.logger.error(f"[SEARCH] An unexpected error occurred. Aborting search.", exc_info=True)
            ending_status = e
        finally:
            await self.web_server.broadcast_state(custom_status=ending_status)
            if search_started_recording:
                try:
                    await self.drone.send_recording_command("stop_recording")
                except DroneError as e:
                    self.logger.error(f"[SEARCH] Failed to stop recording: {e}")
            await self.chat_manager.reset_session()

            # save state of the search, if broken
            if moves_performed < move_limit:
                self.mission_context.search_interrupted = True
                self.mission_context.last_chat_name = name
                self.mission_context.moves_performed = moves_performed

    # If user want to start fresh.
    async def clear_search(self):
        self.mission_context.search_interrupted = False
        self.mission_context.last_chat_name = None
        self.mission_context.moves_performed = 0

    ''' -------------- HELPER METHODS --------------'''

    async def _initialize_or_restore_search_session(self, name: str) -> int:
        """Initializes a new search session or restores an interrupted one."""
        if self.mission_context.search_interrupted and name == self.mission_context.last_chat_name:
            self.logger.info("[SEARCH] Restoring interrupted session.")
            self.mission_context.search_interrupted = False
            await self.chat_manager.restore_session(self.mission_context.last_chat_name)

            # Restore the number of moves performed before the interruption.
            moves_performed = self.mission_context.moves_performed
            self.mission_context.last_chat_name = None
            self.mission_context.moves_performed = 0
            return moves_performed
        else:
            self.logger.info("[SEARCH] Starting new session.")
            await self.chat_manager.create_new_session()
            await self.chat_manager.save_session(name)
            return 0

    # TODO - I think there should be no found - we are only accepting moves.
    async def _confirm_send(self, move=None, found=False):

        self.logger.info("Asking user to confirm the move.")

        # Prepare future variable for the web gui.
        loop = asyncio.get_running_loop()
        self.mission_context.current_decision_future = loop.create_future()

        # We are letting GUI know, that we are waiting for the decision.
        await self.web_server.broadcast_state(waiting_for_decision=True)

        cli_task = asyncio.create_task(self.cli_handler.ask_move_confirmation(move, found))

        # We are waiting for either GUI or CLI
        done, pending = await asyncio.wait(
            [cli_task, self.mission_context.current_decision_future],
            return_when=asyncio.FIRST_COMPLETED
        )

        result_task = done.pop()
        decision = result_task.result()

        for task in pending:
            task.cancel()

        self.mission_context.current_decision_future = None
        return decision

    async def _handle_search(self, args):
        """ Handle search command - parse the arguments and send them further. """
        name, kind, kv = parse_search_arguments(args)
        await self.search(name, kind, kv)

    async def _handle_prompt_cmd(self, args):
        """ Handle prompt command - parse the arguments and send them further. """
        kind, kv = parse_prompt_arguments(args)
        self.prompt_manager.generate_and_save(kind, kv)

    async def _handle_chat_reset(self):

        ans = await self.cli_handler.ask_chat_reset()

        if ans:
            await self.chat_manager.reset_session()
            self.logger.info("Chat deleted.")
        else:
            self.logger.info("Chat not deleted.")

    async def _handle_get_recordings(self) -> None:
        ack = await self.drone.send_get_recordings()
        recordings_raw = ack.get("recordings")
        recordings = recordings_raw if isinstance(recordings_raw, list) else []

        if not recordings:
            print("[GET_RECORDINGS] No .h264 recordings available.")
            return

        print(f"[GET_RECORDINGS] Found {len(recordings)} recording(s):")
        for item in recordings:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            size_bytes = item.get("size_bytes")
            mtime = item.get("mtime")
            metadata_exists = item.get("metadata_exists")
            record_fps = item.get("record_fps")
            print(
                f"  {name} | size_bytes={size_bytes} | mtime={mtime} | "
                f"metadata_exists={metadata_exists} | record_fps={record_fps}"
            )

    async def _handle_pull_recordings(self, args: str) -> None:
        raw_tokens = [token.strip() for token in args.replace(",", " ").split()]

        # Normalize and deduplicate recording names.
        names_to_pull = set()
        for token in raw_tokens:
            if token:
                normalized = token if token.lower().endswith(".h264") else f"{token}.h264"
                names_to_pull.add(normalized)

        names = list(names_to_pull)

        if not names:
            print("Usage: PULL_RECORDINGS <name.h264> [name2.h264 ...]")
            return

        ack = await self.drone.send_pull_recordings(names=names)

        ack_results_raw = ack.get("results")
        ack_results = ack_results_raw if isinstance(ack_results_raw, list) else []

        processed_raw = ack.get("processed_results")
        processed = processed_raw if isinstance(processed_raw, list) else []

        # Create a map for quick lookup of local processing results.
        processed_map = {
            item.get("name"): item
            for item in processed
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }

        print(
            "[PULL_RECORDINGS] "
            f"requested={ack.get('requested_count')} completed={ack.get('completed_count')} "
            f"ok={ack.get('ok')}"
        )

        # Display the status for each requested recording.
        for item in ack_results:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            ok = bool(item.get("ok", False))
            error = item.get("error")
            pulled = processed_map.get(name) if isinstance(name, str) else None

            if not ok:
                print(f"  {name}: pull_failed error={error}")
                continue

            if not isinstance(pulled, dict):
                print(f"  {name}: pulled but no local processing summary")
                continue

            # Display details of the local processing (e.g., conversion to MP4).
            convert_ok = bool(pulled.get("convert_ok", False))
            mp4_path = pulled.get("mp4_path")
            convert_error = pulled.get("convert_error")
            fps_used = pulled.get("fps_used")
            raw_path = pulled.get("raw_path")
            print(
                f"  {name}: raw={raw_path} convert_ok={convert_ok} "
                f"mp4={mp4_path} fps={fps_used} err={convert_error}"
            )

    async def _signal_handler_wrapper(self):
        self._signal_handler()

    def _signal_handler(self):
        """ Function for soft handling of SIGINT """
        if not self.mission_context.stop.is_set():
            print("[WS] shutdown requested (signal). Closing clients…")
            self.mission_context.stop.set()


if __name__ == "__main__":
    mission = MissionControl()
    try:
        asyncio.run(mission.run())
    except KeyboardInterrupt:
        pass
