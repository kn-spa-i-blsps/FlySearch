from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from mission_control.core.action_status import ActionStatus
from mission_control.core.exceptions import DroneError, VLMError, ChatError


class CLIHandler:

    def __init__(self, mission_context, commands):
        self.mission_context = mission_context
        self.commands = commands
        self.cli = PromptSession()  # CLI manager, e.g. commands history.

    async def serve(self):
        """ Handling commands received from the user.

            Parses input and forwards it to the proper method.
        """
        self.print_help()

        with patch_stdout():
            while not self.mission_context.stop.is_set():
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
                    except ValueError:  # Incorrect arguments.
                        self.print_help()
                    except Exception as e:
                        print(f"[ERROR] An unexpected command failure occurred: {e}")
                else:
                    self.print_help()

    async def ask_move_confirmation(self, move=None, found=False):
        print("\n--- COMMAND PREVIEW ---")
        if found:
            print("ACTION: FOUND")
            return ActionStatus.FOUND
        elif move:
            x, y, z = move
            print(f"MOVE: (x={x}, y={y}, z={z})")
        print("Press Enter to send, or type 'no' to cancel, or 'w' to warn vlm.")

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

    async def ask_chat_reset(self):
        print("Are you sure you want to reset this chat? You can use CHAT_SAVE to save it first.")
        print("Type 'yes' to reset.")

        try:
            ans = await self.cli.prompt_async("> ")
        except (EOFError, KeyboardInterrupt):
            ans = "no"

        if ans.lower() == "yes":
            return True
        else:
            return False

    @staticmethod
    def print_help():
        print("Perform search:")
        print("    SEARCH <name> <FS-1|FS-2> [object=.. glimpses=.. area=.. minimum_altitude=..]")

        print("Chat management:")
        print("    CHAT_INIT | CHAT_RESET | CHAT_SAVE <name> | CHAT_RETRIEVE <name>")

        print("Prompt manager:")
        print("    PROMPT FS-1|FS-2 [object=.. glimpses=.. area=.. minimum_altitude=..]")

        print("Drone communication:")
        print(
            "    PHOTO_WITH_TELEMETRY | START_RECORDING | STOP_RECORDING | GET_RECORDINGS | PULL_RECORDINGS <names> | MOVE")

        print("VLM communication:")
        print("    SEND_TO_VLM | ADD_WARNING")