import asyncio
import signal
from typing import Dict, Callable, Awaitable

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from mission_control.bridges.drone_bridge import DroneBridge
from mission_control.bridges.vlm_bridge import VLMBridge
from mission_control.core.action_status import ActionStatus
from mission_control.core.config import Config
from mission_control.core.mission_context import MissionContext
from mission_control.managers.prompt_manager import PromptManager
from mission_control.utils.parsers import parse_prompt_arguments


# FUTURE:
#  - simple html showing photo, reasoning, and proposed move with few options to choose for the user.
#  - move chat management to the other class?

# TODO:
#  - our own exceptions. Api methods should raise exceptions. Mission control should catch them and handle them.
#     example: NoDroneConnected("Can't perform search - no drone is connected")
#  - check default values in parsed.move i parsed.found


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

        # Dispatcher - maps command name with proper function/method.
        self.commands: Dict[str, Callable[[str, str], Awaitable[None]]] = {

            "search": lambda _, args: self._handle_search(args),

            "chat_init": lambda c, a: self.vlm.chat_init(),
            "chat_save": lambda _, args: self.vlm.chat_save(args),
            "chat_retrieve": lambda _, args: self.vlm.chat_retrieve(args),
            "chat_reset": lambda c, a: self.vlm.chat_reset(),

            "prompt": lambda _, args: self._handle_prompt_cmd(args),

            "photo_with_telemetry": lambda cmd, _: self.drone.send_message(cmd),
            "move": lambda c, a: self.drone.send_command(
                found=self.mission_context.parsed_response.found,
                move=self.mission_context.parsed_response.move
            ),

            "send_to_vlm": lambda c, a: self.vlm.send_to_vlm(),
            "add_warning": lambda c, a: self.vlm.send_to_vlm(
                is_warning=True
            ),

            "q":    lambda c, a: self._signal_handler(),
            "quit": lambda c, a: self._signal_handler(),
            "exit": lambda c, a: self._signal_handler()
        }

    ''' -------------- ASYNC LOOP METHOD -------------- '''
    async def run(self):
        """ Main function for async loop. """
        loop = asyncio.get_running_loop()

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

        # Start those two method concurrently.
        repl_task = asyncio.create_task(self.stdin_repl()) # CLI
        stop_task = asyncio.create_task(self.stop.wait()) # signal handler

        # Wait for the first one to complete.
        done, pending = await asyncio.wait(
            [repl_task, stop_task],
            return_when=asyncio.FIRST_COMPLETED
        )

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

                line = (line or " ").strip()
                if not line:
                    continue

                # Unify.
                cmd = line.lower()

                #Split command from arguments.
                parts = cmd.split(" ", 1)
                command = parts[0]
                args = parts[1] if len(parts) > 1 else ""

                # Take and use the method from those defined in __init__.
                handler = self.commands.get(command)

                if handler:
                    try:
                        await handler(command, args)
                    except ValueError:                  # Incorrect arguments.
                        print_help()
                    except Exception as e:
                        print(f"[ERROR] Command failed: {e}")
                else:
                    print_help()

    ''' -------------- WHOLE SEARCH SEQUENCE -------------- '''
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
        # Initial prompt.
        self.prompt_manager.generate_and_save(kind, kv)

        # Init vlm chat.
        await self.vlm.chat_init()
        await self.vlm.chat_save("autosave")

        ret = ActionStatus.CONFIRMED
        moves_performed = 0
        move_limit = kv["glimpses"]

        while (ret in [ActionStatus.CONFIRMED, ActionStatus.WARNING]
               and moves_performed < move_limit):
            # Request photo and telemetry.
            await self.drone.send_message("photo_with_telemetry")

            # Send it to vlm.
            await self.vlm.send_to_vlm(is_warning=(ret == ActionStatus.WARNING))

            # Autosave the chat.
            await self.vlm.chat_save("autosave")

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

    ''' -------------- HELPER METHODS --------------'''
    async def _confirm_send(self, move=None, found=False):
        print("\n--- COMMAND PREVIEW ---")
        if found:
            print("ACTION: FOUND")
            return ActionStatus.FOUND
        elif move:
            x, y, z = move
            print(f"MOVE: (x={x}, y={y}, z={z})")
        print("Press Enter to send, or type 'no' to cancel,"
              " or 'w' to warn vlm (continue search, stop this move.")
        with patch_stdout():
            while True:
                try:
                    ans = await self.cli.prompt_async("> ")
                except (EOFError, KeyboardInterrupt):
                    ans = "no"

                if ans.strip().lower() in ("", "y", "yes"):
                    return ActionStatus.CONFIRMED
                elif ans.strip().lower() in ("w", "warning", "warn"):
                    return ActionStatus.WARNING
                elif ans.strip().lower() in ("no", "n"):
                    return ActionStatus.CANCELLED

    async def _handle_search(self, args):
        """ Handle search command - parse the arguments and send them further. """
        kind, kv = parse_prompt_arguments(args)
        await self.search(kind, kv)

    async def _handle_prompt_cmd(self, args):
        """ Handle prompt command - parse the arguments and send them further. """
        kind, kv = parse_prompt_arguments(args)
        self.prompt_manager.generate_and_save(kind, kv)

    def _signal_handler(self):
        """ Function for soft handling of SIGINT """
        if not self.stop.is_set():
            print("\n[WS] shutdown requested (signal). Closing clients…")
            self.stop.set()


def print_help():
    print("Perform search:")
    print("    SEARCH FS-1|FS-2 [object=.. glimpses=.. area=..]")

    print("Chat management:")
    print("    CHAT_INIT | CHAT_RESET | CHAT_SAVE <name> | CHAT_RETRIEVE <name>")

    print("Prompt manager:")
    print("    PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..]")

    print("Drone communication:")
    print("    PHOTO_WITH_TELEMETRY | MOVE")

    print("VLM communication:")
    print("    SEND_TO_VLM | ADD_WARNING")


if __name__ == "__main__":
    mission = MissionControl()
    try:
        asyncio.run(mission.run())
    except KeyboardInterrupt:
        pass