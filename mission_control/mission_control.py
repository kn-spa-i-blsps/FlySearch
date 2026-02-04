import asyncio
import signal
from typing import Dict, Callable, Awaitable

from Pillow import Image
from websockets.frames import CloseCode

from mission_control.bridges.drone_bridge import DroneBridge
from mission_control.bridges.vlm_bridge import VLMBridge
from mission_control.core.config import Config
from mission_control.core.mission_context import MissionContext
from mission_control.managers.prompt_manager import PromptManager
from mission_control.utils.parsers import parse_prompt_arguments


# TODO:
#  - clean up VLMBridge
# FUTURE:
#  - simple html showing photo, reasoning, and proposed move with few options from the user.


class MissionControl:
    def __init__(self):
        self.config = Config()
        self.mission_context = MissionContext()

        self.prompt_manager = PromptManager(self.config, self.mission_context)

        self.stop = asyncio.Event()

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


        while not self.stop.is_set():
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
                self._signal_handler()
                break

            # Take and use the method from those defined in __init__
            handler = self.commands.get(command)

            if handler:
                try:
                    await handler(command, args)
                except ValueError:
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
                loop.add_signal_handler(sig, self._signal_handler)
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
                await task  # Wait for the confirmation
            except asyncio.CancelledError:
                pass

        # Stop the WebSocket connection.
        await self.drone.stop()

    def _signal_handler(self):
        """ Function for soft handling of SIGINT """
        if not self.stop.is_set():
            print("\n[WS] shutdown requested (signal). Closing clients…")
            self.stop.set()

''' --------------------------- OTHER FUNCTIONS -------------------------- '''

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