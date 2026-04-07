import asyncio

from mission_control.vlm.vlm_bridge import FlySearchVLMBridge
from mission_control.core.config import Config
from mission_control.vlm.chat_storage_helper import FileChatStorageHelper
from mission_control.prompt_helpers.prompt_helper import FlySearchPromptHelper
from mission_control.mission.mission_manager import MissionManager
from mission_control.ui.web_server import WebServer
from mission_control.utils.event_bus import MemoryEventBus
from mission_control.utils.logger import get_configured_logger

logger = get_configured_logger(__name__)

async def main():
    config = Config()
    event_bus = MemoryEventBus()
    logger.debug("[MAIN] Event Bus created.")

    storage = FileChatStorageHelper(config.chats_dir)
    vlm_bridge = FlySearchVLMBridge(config, event_bus, storage)
    logger.debug("[MAIN] VLMBridge created.")
    # drone_bridge = WebSocketDroneBridge(config, event_bus)
    logger.debug("[MAIN] DroneBridge created.")
    prompts = FlySearchPromptHelper(config)
    mission_manager = MissionManager(event_bus, prompts)
    logger.debug("[MAIN] Mission Manager created.")
    logger.debug("[MAIN] Search Orchestrator created.")

    logger.info("[MAIN] Starting DroneBridge, CLIHandler and the WebServer.")

    # try:
    #     drone_bridge.start()
    # except DroneCommunicationError as e:
    #     logger.error(e)
    #     exit(1)
    #cli_handler = CLIHandler(event_bus)
    web_server = WebServer(config, event_bus)

    #repl_task = asyncio.create_task(cli_handler.serve())
    # WEB GUI
    web_task = asyncio.create_task(web_server.serve())

    done, pending = await asyncio.wait(
        [
            #repl_task,
            web_task],
        return_when=asyncio.FIRST_COMPLETED
    )

    web_server.request_stop()

    # Cancel those which haven't completed yet.
    for task in pending:
        task.cancel()
        try:
            await task  # Wait for the confirmation.
        except asyncio.CancelledError:
            pass

    # Waiting for CLIHandler to end...

    #drone_bridge.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass