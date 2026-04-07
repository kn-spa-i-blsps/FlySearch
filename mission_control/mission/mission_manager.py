import logging
from typing import Dict

from mission_control.core.events import StartMissionCommand
from mission_control.core.interfaces import EventBus, PromptHelper
from mission_control.mission.search_orchestrator import SearchOrchestrator

logger = logging.getLogger(__name__)


class MissionManager:
    """ Zarządca Cyklu Życia. Słucha komend startowych i rodzi nowe Sagi (Orkiestratory). """

    def __init__(self, event_bus: EventBus, prompts: PromptHelper):
        self.event_bus = event_bus
        self.prompts = prompts

        # Słownik przechowujący aktywne instancje Orkiestratorów (mission_id -> SearchOrchestrator)
        self.active_missions: Dict[str, SearchOrchestrator] = {}

        # Menedżer jest jedynym, który nasłuchuje na globalny start misji
        self.event_bus.subscribe(StartMissionCommand, self.handle_start_mission)

    async def handle_start_mission(self, event: StartMissionCommand):
        mission_id = event.mission_id

        if mission_id in self.active_missions:
            logger.warning(f"[MANAGER] Mission {mission_id} is already running!")
            return

        logger.info(f"[MANAGER] Spawning new Orchestrator for mission: {mission_id}")

        # 1. Tworzymy nowy obiekt Orkiestratora na wyłączność tej misji
        orchestrator = SearchOrchestrator(self.event_bus, self.prompts)

        # 2. Zapisujemy w pamięci, żeby Garbage Collector go nie usunął
        self.active_missions[mission_id] = orchestrator

        # 3. Ręcznie odpalamy proces startowy
        await orchestrator.start(event)