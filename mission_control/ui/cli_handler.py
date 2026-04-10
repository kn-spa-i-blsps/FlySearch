import asyncio
import signal
from typing import Dict, Callable, Awaitable, Any

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from mission_control.core.action_status import ActionStatus
from mission_control.core.events import AskUserConfirmationCommand, UserDecisionReceived, StartMissionCommand, \
    SearchEnded
from mission_control.core.interfaces import EventBus
from mission_control.utils.parsers import parse_search_arguments


class CLIHandler:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.stop = asyncio.Event()
        self.active_mission_id = None
        self.pending_confirmation = None
        self.cli = PromptSession()  # CLI manager, e.g. commands history.

        self.event_bus.subscribe(AskUserConfirmationCommand, self.ask_move_confirmation)
        self.event_bus.subscribe(SearchEnded, self.handle_search_end)

        self.commands: Dict[str, Callable[[str], Awaitable[Any]]] = {
            "search": self._handle_search,
            "quit": self._handle_quit
        }

    async def serve(self):
        """ Handling commands received from the user.

            Parses input and forwards it to the proper method.
        """
        loop = asyncio.get_running_loop()

        # Instead of closing, use _signal_handler function
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._signal_handler)
            except NotImplementedError:
                pass

        self.print_help()

        with patch_stdout():
            while not self.stop.is_set():
                prompt_text = "[CONFIRM Y/W/N] > " if self.pending_confirmation else "> "

                try:
                    line = await self.cli.prompt_async(prompt_text)
                except (EOFError, KeyboardInterrupt):
                    line = "quit"

                raw_line = (line or " ").strip()
                if not raw_line:
                    continue

                if self.pending_confirmation:
                    await self._process_user_decision(raw_line)
                    continue

                # Keep original argument casing, normalize only command name.
                try:
                    command, args = raw_line.split(" ", 1)
                    command = command.lower()
                    args = args.strip()
                except ValueError:
                    command = raw_line.lower()
                    args = ""

                # Take and send the event from those defined in __init__.
                handler = self.commands.get(command, None)

                if handler:
                    try:
                        await handler(args)
                    except ValueError:  # Incorrect arguments.
                        self.print_help()
                    except Exception as e:
                        print(f"[ERROR] An unexpected command failure occurred: {e}")
                else:
                    self.print_help()

    async def ask_move_confirmation(self, event: AskUserConfirmationCommand):
        if self.active_mission_id != event.mission_id:
            return

        self.pending_confirmation = event

        print("\n--- COMMAND PREVIEW ---")
        x, y, z = event.move
        print(f"MOVE: (x={x}, y={y}, z={z})")
        print("Press Enter to send, or type 'no' to cancel, or 'w' to warn vlm.")

    async def handle_search_end(self, event: SearchEnded):
        if self.active_mission_id != event.mission_id:
            return

        self.active_mission_id = None
        self.pending_confirmation = None
        print("\n--- SEARCH ENDED ---")
        print("The object was ", "" if event.found else "not ", "found.")
        print(f"Moves performed: {event.moves_performed}")

    async def _process_user_decision(self, user_input: str):
        ans = user_input.strip().lower()

        if ans in ("", "y", "yes"):
            decision = ActionStatus.CONFIRMED
        elif ans in ("w", "warning", "warn"):
            decision = ActionStatus.WARNING
        elif ans in ("no", "n"):
            decision = ActionStatus.CANCELLED
        else:
            print("Invalid input. Please press Enter (yes), 'w' (warn), or 'no' (cancel).")
            return

        decision_event = UserDecisionReceived(
            mission_id=self.active_mission_id,
            decision=decision,
            move=self.pending_confirmation.move,
        )
        await self.event_bus.publish(decision_event)
        self.pending_confirmation = None

    async def _handle_search(self, args: str):
        mission_id, drone_id, kind, kv = parse_search_arguments(args)
        self.active_mission_id = mission_id
        event = StartMissionCommand(
            mission_id=mission_id,
            drone_id=drone_id,
            prompt_type=kind,
            prompt_args=kv,
        )
        await self.event_bus.publish(event)

    async def _handle_quit(self, args: str = ""):
        """ Function for soft handling of SIGINT """
        if not self.stop.is_set():
            print("[CLI] shutdown requested (signal). Closing clients…")
            self.stop.set()

    def _signal_handler(self):
        """ Synchronous function for OS signals (SIGINT, SIGTERM) """
        if not self.stop.is_set():
            print("\n[CLI] Shutdown requested (signal). Closing clients…")
            self.stop.set()

    @staticmethod
    def print_help():
        print("Perform search:")
        print("    SEARCH <name> <FS-1|FS-2> [object=.. glimpses=.. area=.. minimum_altitude=..]")

        # print("Chat management:")
        # print("    CHAT_INIT | CHAT_RESET | CHAT_SAVE <name> | CHAT_RETRIEVE <name>")
        #
        # print("Prompt manager:")
        # print("    PROMPT FS-1|FS-2 [object=... glimpses=... area=... minimum_altitude=...]")
        #
        # print("Drone communication:")
        # print(
        #     "    PHOTO_WITH_TELEMETRY | START_RECORDING | STOP_RECORDING | GET_RECORDINGS | PULL_RECORDINGS <names> | MOVE")
        #
        # print("VLM communication:")
        # print("    SEND_TO_VLM | ADD_WARNING")
