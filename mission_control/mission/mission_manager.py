import logging
from typing import Dict

from mission_control.core.events import StartMissionCommand, SearchEnded
from mission_control.core.interfaces import EventBus, PromptHelper
from mission_control.mission.search_orchestrator import SearchOrchestrator

logger = logging.getLogger(__name__)


class MissionManager:
    """ Manages new orchestrators' creation. """

    def __init__(self, event_bus: EventBus, prompts: PromptHelper):
        self.event_bus = event_bus
        self.prompts = prompts
        self.active_missions: Dict[str, SearchOrchestrator] = {}
        self.event_bus.subscribe(StartMissionCommand, self.handle_start_mission)
        self.event_bus.subscribe(SearchEnded, self.handle_mission_ended)

    async def handle_start_mission(self, event: StartMissionCommand):
        mission_id = event.mission_id

        if mission_id in self.active_missions:
            logger.warning(f"[MISSION MANAGER] Mission {mission_id} is already running!")
            return

        logger.info(f"[MISSION MANAGER] Spawning new Orchestrator for mission: {mission_id}")

        orchestrator = SearchOrchestrator(self.event_bus, self.prompts)
        self.active_missions[mission_id] = orchestrator
        await orchestrator.start(event)

    async def handle_mission_ended(self, event: SearchEnded):
        mission_id = event.mission_id

        if mission_id not in self.active_missions:
            logger.warning(f"[MISSION MANAGER] SearchEnded event, but mission {mission_id} is not running!")
            return

        logger.info(f"[MISSION MANAGER] Deleting Orchestrator for mission: {mission_id}")

        self.active_missions.pop(mission_id)