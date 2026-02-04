import asyncio
import signal
from typing import Dict, Callable, Awaitable

from Pillow import Image
from websockets.frames import CloseCode

from mission_control.bridges.drone_bridge import DroneBridge
from mission_control.bridges.vlm_bridge import VLMBridge
from mission_control.core.action_status import ActionStatus
from mission_control.core.config import Config
from mission_control.core.mission_context import MissionContext
from mission_control.managers.prompt_manager import PromptManager
from mission_control.utils.parsers import parse_prompt_arguments

# FUTURE:
#  - simple html showing photo, reasoning, and proposed move with few options to choose for the user.


class MissionControl:
    def __init__(self):
        self.config = Config()                      # Configuration variables - dirs, ports, hosts...
        self.mission_context = MissionContext()     # Holds useful info like last photo taken.

        self.stop = asyncio.Event()                 # todo: idk yet lmao

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
            self.drone
        )

        # Dispatcher - maps command name with proper function/method.
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

            "prompt": self._handle_prompt_cmd,

            "q": lambda c, a: self._signal_handler(),
            "quit": lambda c, a: self._signal_handler(),
            "exit": lambda c, a: self._signal_handler()
        }

    async def _handle_search(self, cmd, args):
        """ Handle search command - parse the arguments and send them further. """
        kind, kv = parse_prompt_arguments(args)
        await self.search(kind, kv)

    async def _handle_prompt_cmd(self, cmd, args):
        """ Handle prompt command - parse the arguments and send them further. """
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
        # Initial prompt.
        self.prompt_manager.generate_and_save(kind, kv)

        # Request first photo and telemetry.
        await self.drone.send_message("photo_with_telemetry")

        # Initialize the chat with vlm - it sends initial prompt
        #  and receives first answer.
        ret = await self.vlm.chat_init()
        await self.vlm.chat_save("autosave")

        moves_performed = 1
        move_limit = kv["glimpses"]

        #TODO: What happens if the goal is found?
        # Should we add another action status?
        while (ret in [ActionStatus.CONFIRMED, ActionStatus.WARNING]
               and moves_performed < move_limit):
            # Request photo and telemetry.
            await self.drone.send_message("photo_with_telemetry")

            # Send it to vlm and wait for the user interaction.
            ret = await self.vlm.send_to_vlm()

            # Autosave the chat.
            await self.vlm.chat_save("autosave")

            # Count the move as performed only if it was accepted by the user.
            if ret == ActionStatus.CONFIRMED:
                moves_performed += 1

    async def stdin_repl(self):
        """ Handling commands received from the user.

            Parses input and forwards it to the proper method.
        """

        loop = asyncio.get_running_loop()

        print_help()

        while not self.stop.is_set():
            # Take the input from the user.
            try:
                # TODO: fix
                line = await loop.run_in_executor(None, input, "> ")
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
                except Exception as e:              # TODO: does any of these functions throw valerr?
                    print(f"[ERROR] Command failed: {e}")
            else:
                print_help()

    async def run(self):
        """ Main function for async loop. """
        loop = asyncio.get_running_loop()

        # Instead of closing, use _signal_handler function
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._signal_handler)
            except NotImplementedError:
                pass

        # Start the WebSocket connection and listen for the drone.
        try:
            await self.drone.start()
        except OSError:
            print("[CRITICAL] Failed to start drone bridge. Exiting.")
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

    def _signal_handler(self):
        """ Function for soft handling of SIGINT """
        if not self.stop.is_set():
            print("\n[WS] shutdown requested (signal). Closing clients…")
            self.stop.set()


def print_help():
    print("Commands: PHOTO_WITH_TELEMETRY | SEND_PHOTO | TELEMETRY | "
          "PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..] | q")
    print("          CHAT_INIT | CHAT_RESET | CHAT_SAVE <name> | "
          "CHAT_RETRIEVE <name> | SEND_TO_VLM")


if __name__ == "__main__":
    mission = MissionControl()
    try:
        asyncio.run(mission.run())
    except KeyboardInterrupt:
        pass