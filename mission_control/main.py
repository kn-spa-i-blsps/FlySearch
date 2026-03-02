import asyncio
import signal
from typing import Dict, Callable, Awaitable

import uvicorn
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from mission_control.bridges.drone_bridge import DroneBridge
from mission_control.bridges.vlm_bridge import VLMBridge
from mission_control.core.action_status import ActionStatus
from mission_control.core.config import Config
from mission_control.core.exceptions import DroneError, VLMError, ChatError
from mission_control.core.mission_context import MissionContext
from mission_control.managers.chat_manager import ChatSessionManager
from mission_control.managers.prompt_manager import PromptManager
from mission_control.utils.parsers import parse_prompt_arguments, parse_search_arguments
from mission_control.web_server import WebServer


# FUTURE:
#  - simple html showing photo, reasoning, and proposed move with few options to choose for the user.

class MissionControl:
    def __init__(self):
        self.config = Config()                      # Configuration variables - dirs, ports, hosts...
        self.mission_context = MissionContext()     # All connected modules returns info there.

        self.stop = asyncio.Event()                 # Interrupt flag.

        self.cli = PromptSession()                  # CLI manager, e.g. commands history.

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

        self.web_server = WebServer(self.mission_context)           # GUI

        # Dispatcher - maps command name with proper function/method.
        self.commands: Dict[str, Callable[[str, str], Awaitable[None]]] = {

            "search": lambda _, args: self._handle_search(args),

            "chat_init": lambda c, a: self.chat_manager.create_new_session(),
            "chat_save": lambda _, args: self.chat_manager.save_session(args),
            "chat_retrieve": lambda _, args: self.chat_manager.restore_session(args),
            "chat_reset": lambda c, a: self._handle_chat_reset(),

            "prompt": lambda _, args: self._handle_prompt_cmd(args),

            "photo_with_telemetry": lambda cmd, _: self.drone.send_message(cmd),
            "start_recording": lambda cmd, _: self.drone.send_recording_command(cmd),
            "stop_recording": lambda cmd, _: self.drone.send_recording_command(cmd),
            "get_recordings": lambda _cmd, _args: self._handle_get_recordings(),
            "pull_recordings": lambda _cmd, args: self._handle_pull_recordings(args),
            "move": lambda c, a: self.drone.send_command(
                found=self.mission_context.parsed_response.found,
                move=self.mission_context.parsed_response.move
            ),

            "send_to_vlm": lambda c, a: self.vlm.send_to_vlm(),
            "add_warning": lambda c, a: self.vlm.send_to_vlm(
                is_warning=True
            ),

            "q":    lambda c, a: self._signal_handler_wrapper(),
            "quit": lambda c, a: self._signal_handler_wrapper(),
            "exit": lambda c, a: self._signal_handler_wrapper()
        }

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
        except OSError:
            print("[Mission Control] CRITICAL: Failed to start drone bridge. Exiting.")
            return

        # CLI
        repl_task = asyncio.create_task(self.stdin_repl())
        # WEB GUI
        web_task = asyncio.create_task(self.web_server.serve())
        # Stop signal
        stop_task = asyncio.create_task(self.stop.wait())

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

    ''' -------------- CLI HANDLING -------------- '''
    async def stdin_repl(self):
        """ Handling commands received from the user.

            Parses input and forwards it to the proper method.
        """
        print_help()

        with patch_stdout():
            while not self.stop.is_set():
                # Take the input from the user.
                try:
                    line = await self.cli.prompt_async("> ")
                except (EOFError, KeyboardInterrupt):
                    line = "q"

                raw_line = (line or " ").strip()
                if not raw_line:
                    continue

                # Keep original argument casing, normalize only command name.
                parts = raw_line.split(" ", 1)
                command = parts[0].lower()
                args = parts[1].strip() if len(parts) > 1 else ""

                # Take and use the method from those defined in __init__.
                handler = self.commands.get(command)

                if handler:
                    try:
                        await handler(command, args)
                    except (DroneError, VLMError, ChatError) as e:
                        print(f"[ERROR] {e}")
                    except ValueError:                  # Incorrect arguments.
                        print_help()
                    except Exception as e:
                        print(f"[ERROR] An unexpected command failure occurred: {e}")
                else:
                    print_help()

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
        print("\n--- SEARCHING... ---")
        search_started_recording = False
        try:

            await self.web_server.broadcast_state(custom_status="Search in progress... Initializing VLM.")

            # Initial prompt.
            await self.drone.send_recording_command("start_recording")
            search_started_recording = True
            self.prompt_manager.generate_and_save(kind, kv)

            # Init vlm chat.
            await self.chat_manager.create_new_session()
            await self.chat_manager.save_session(name)

            ret = ActionStatus.CONFIRMED
            moves_performed = 0
            move_limit = int(kv["glimpses"])

            while (ret in [ActionStatus.CONFIRMED, ActionStatus.WARNING]
                   and moves_performed < move_limit):
                # Request photo and telemetry.
                self.mission_context.photo_received_event.clear()
                await self.drone.send_message("photo_with_telemetry")

                try:
                    await asyncio.wait_for(self.mission_context.photo_received_event.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    print("[ERROR] Timeout: Photo not received. Aborting search.")
                    break

                # Send it to vlm.
                await self.vlm.send_to_vlm(is_warning=(ret == ActionStatus.WARNING))

                # Autosave the chat.
                await self.chat_manager.save_session(name)

                # Take parsed response and ask for confirmation.
                parsed = self.mission_context.parsed_response

                ret = await self._confirm_send(found=parsed.found, move=parsed.move)

                if ret == ActionStatus.CONFIRMED:
                    # If confirmed, send the move to the drone.
                    await self.drone.send_command(found=parsed.found, move=parsed.move)
                    moves_performed += 1
                elif ret == ActionStatus.FOUND:
                    # If found, print the message and end the loop.
                    print("FOUND")
        except (DroneError, VLMError, ChatError) as e:
            print(f"[SEARCH FAILED] An error occurred: {e}")
            print("Aborting search.")
            await self.web_server.broadcast_state(custom_status=e)
        except Exception as e:
            print(f"[SEARCH FAILED] An unexpected error occurred: {e}")
            print("Aborting search.")
            await self.web_server.broadcast_state(custom_status=e)
        finally:
            if search_started_recording:
                try:
                    await self.drone.send_recording_command("stop_recording")
                except DroneError as e:
                    print(f"[WARN] Failed to stop recording: {e}")

    ''' -------------- HELPER METHODS --------------'''

    async def _confirm_send(self, move=None, found=False):
        print("\n--- COMMAND PREVIEW ---")
        if found:
            print("ACTION: FOUND")
            return ActionStatus.FOUND
        elif move:
            x, y, z = move
            print(f"MOVE: (x={x}, y={y}, z={z})")
        print("Press Enter to send, or type 'no' to cancel, or 'w' to warn vlm.")

        # Prepare future variable.
        loop = asyncio.get_running_loop()
        self.mission_context.current_decision_future = loop.create_future()

        # We are letting GUI know, that we are waiting for the decision.
        await self.web_server.broadcast_state(waiting_for_decision=True)

        # CLI reading.
        async def cli_waiter():
            while True:
                try:
                    ans = await self.cli.prompt_async("> ")
                except (EOFError, KeyboardInterrupt):
                    ans = "no"

                ans = ans.strip().lower()
                if ans in ("", "y", "yes"):
                    return ActionStatus.CONFIRMED
                elif ans in ("w", "warning", "warn"):
                    return ActionStatus.WARNING
                elif ans in ("no", "n"):
                    return ActionStatus.CANCELLED

        cli_task = asyncio.create_task(cli_waiter())

        # We are waiting for either GUI or CLI
        done, pending = await asyncio.wait(
            [cli_task, self.mission_context.current_decision_future],
            return_when=asyncio.FIRST_COMPLETED
        )

        result_task = done.pop()
        decision = result_task.result()

        for task in pending:
            task.cancel()

        self.current_decision_future = None
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

        print("Are you sure you want to reset this chat? You can use CHAT_SAVE to save it first.")
        print("Type 'yes' to reset.")

        try:
            ans = await self.cli.prompt_async("> ")
        except (EOFError, KeyboardInterrupt):
            ans = "no"

        if ans.lower() == "yes":
            await self.chat_manager.reset_session()
            print("Chat deleted.")
        else:
            print("Chat not deleted.")

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
        raw_names = [token.strip() for token in args.replace(",", " ").split()]
        names: list[str] = []
        for name in raw_names:
            if not name:
                continue
            normalized = name if name.lower().endswith(".h264") else f"{name}.h264"
            if normalized not in names:
                names.append(normalized)

        if not names:
            print("Usage: PULL_RECORDINGS <name.h264> [name2.h264 ...]")
            return

        ack = await self.drone.send_pull_recordings(names=names)
        ack_results_raw = ack.get("results")
        ack_results = ack_results_raw if isinstance(ack_results_raw, list) else []

        processed_raw = ack.get("processed_results")
        processed = processed_raw if isinstance(processed_raw, list) else []
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
        if not self.stop.is_set():
            print("[WS] shutdown requested (signal). Closing clients…")
            self.stop.set()


def print_help():
    print("Perform search:")
    print("    SEARCH <name> <FS-1|FS-2> [object=.. glimpses=.. area=.. minimum_altitude=..]")

    print("Chat management:")
    print("    CHAT_INIT | CHAT_RESET | CHAT_SAVE <name> | CHAT_RETRIEVE <name>")

    print("Prompt manager:")
    print("    PROMPT FS-1|FS-2 [object=.. glimpses=.. area=.. minimum_altitude=..]")

    print("Drone communication:")
    print("    PHOTO_WITH_TELEMETRY | START_RECORDING | STOP_RECORDING | GET_RECORDINGS | PULL_RECORDINGS <names> | MOVE")

    print("VLM communication:")
    print("    SEND_TO_VLM | ADD_WARNING")


if __name__ == "__main__":
    mission = MissionControl()
    try:
        asyncio.run(mission.run())
    except KeyboardInterrupt:
        pass
