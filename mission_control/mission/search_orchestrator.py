import logging
from enum import Enum, auto

from mission_control.core.action_status import ActionStatus
from mission_control.core.events import PhotoWithTelemetryReceived, VlmAnalysisCompleted, StartMissionCommand, \
    CreateNewSessionCommand, GetPhotoAndTelemetryCommand, AnalyzePhotoCommand, \
    AskUserConfirmationCommand, UserDecisionReceived, ExecuteMoveCommand, SaveSessionCommand, MoveExecuted, SearchEnded, \
    DroneErrorOccurred, VlmErrorOccurred, ChatErrorOccurred, StartRecordingCommand, DroneConnectionLost, \
    StopRecordingCommand, DroneReconnected, DroneDisconnected, MoveStarted, DeleteSessionCommand
from mission_control.core.interfaces import EventBus, PromptHelper

logger = logging.getLogger(__name__)


class MissionState(Enum):
    WAITING_FOR_ACK = auto()
    PAUSED = auto()
    IDLE = auto()
    WAITING_FOR_DRONE = auto()
    WAITING_FOR_VLM = auto()
    WAITING_FOR_USER = auto()
    FLYING = auto()
    ENDED = auto()


class SearchOrchestrator:
    def __init__(self, event_bus: EventBus, prompts: PromptHelper):
        self.pending_command = None
        self.moves_performed = 0
        self.event_bus = event_bus
        self.mission_id: str = ""
        self.initial_prompt: str = ""
        self.drone_id: str = ""
        self.state: MissionState = MissionState.IDLE
        self.is_warning: bool = False
        self.max_moves: int = 0
        self.prompt_helper = prompts
        self.current_move = None

        self.event_bus.subscribe(PhotoWithTelemetryReceived, self.handle_photo_and_telemetry)
        self.event_bus.subscribe(VlmAnalysisCompleted, self.handle_vlm_analysis)
        self.event_bus.subscribe(UserDecisionReceived, self.handle_user_decision)
        self.event_bus.subscribe(MoveExecuted, self.handle_move_executed)
        self.event_bus.subscribe(MoveStarted, self.handle_move_started)
        self.event_bus.subscribe(DroneConnectionLost, self.handle_drone_connection_lost)
        self.event_bus.subscribe(DroneReconnected, self.handle_drone_reconnected)
        self.event_bus.subscribe(DroneDisconnected, self.handle_drone_disconnected)

        self.event_bus.subscribe(DroneErrorOccurred, self.handle_drone_error)
        self.event_bus.subscribe(VlmErrorOccurred, self.handle_vlm_error)
        self.event_bus.subscribe(ChatErrorOccurred, self.handle_chat_error)

    async def start(self, event: StartMissionCommand):
        try:
            self.mission_id = event.mission_id
            self.drone_id = event.drone_id

            kind = event.prompt_type
            kv = event.prompt_args
            glimpses = int(kv.get("glimpses", 0))

            self.max_moves = glimpses - 1
            if self.max_moves == -1:
                raise ValueError("[SEARCH] No glimpses attribute or glimpses set to 0.")

            self.initial_prompt = await self.prompt_helper.generate_prompt(kind, kv)

            await self.event_bus.publish(CreateNewSessionCommand(chat_id=self.mission_id, prompt=self.initial_prompt), wait_for_completion=True)
            await self.event_bus.publish(SaveSessionCommand(chat_id=self.mission_id), wait_for_completion=True)
            if self.state == MissionState.ENDED:
                return
            self.state = MissionState.WAITING_FOR_DRONE
            await self.event_bus.publish(StartRecordingCommand(drone_id=self.drone_id), wait_for_completion=True)
            await self.event_bus.publish(GetPhotoAndTelemetryCommand(drone_id=self.drone_id))
        except Exception as e:
            await self._abort_mission(reason=str(e))
            raise

    async def handle_photo_and_telemetry(self, event: PhotoWithTelemetryReceived):
        if self.drone_id != event.drone_id:
            return
        if self.state != MissionState.WAITING_FOR_DRONE:
            logger.warning("[SEARCH] Photo with telemetry received, but we are not waiting for the drone.")
            return

        command = AnalyzePhotoCommand(
            chat_id=self.mission_id,
            is_warning=self.is_warning,
            photo_path=event.photo_path,
            telemetry_path=event.telemetry_path
        )
        self.is_warning = False
        self.state = MissionState.WAITING_FOR_VLM
        await self.event_bus.publish(command)

    async def handle_vlm_analysis(self, event: VlmAnalysisCompleted):
        if self.mission_id != event.chat_id:
            return
        if self.state != MissionState.WAITING_FOR_VLM:
            logger.warning("[SEARCH] VLM analysis received, but we are not waiting for the vlm.")
            return

        await self.event_bus.publish(SaveSessionCommand(chat_id=self.mission_id))

        if event.found:
            await self._terminate_mission(found=True)
            return

        if self.moves_performed >= self.max_moves:
            await self._terminate_mission(found=False)
            return

        command = AskUserConfirmationCommand(
            mission_id=self.mission_id,
            reasoning=event.reasoning,
            move=event.move
        )
        self.state = MissionState.WAITING_FOR_USER
        await self.event_bus.publish(command)

    async def handle_user_decision(self, event: UserDecisionReceived):
        if self.mission_id != event.mission_id:
            return
        if self.state != MissionState.WAITING_FOR_USER:
            logger.warning("[SEARCH] User decision received, but we are not waiting for the user.")
            return

        if event.decision == ActionStatus.CANCELLED:
            await self._terminate_mission(found=False)
            return

        if event.decision == ActionStatus.WARNING:
            self.is_warning = True
            self.state = MissionState.WAITING_FOR_DRONE
            await self.event_bus.publish(GetPhotoAndTelemetryCommand(drone_id=self.drone_id))

        if event.decision == ActionStatus.CONFIRMED:
            self.current_move = event.move
            command = ExecuteMoveCommand(
                drone_id=self.drone_id,
                move=event.move
            )
            self.state = MissionState.WAITING_FOR_ACK
            await self.event_bus.publish(command)

    # We need to know, in case of connection loss, if command was delivered, and the move performed.
    async def handle_move_started(self, event: MoveStarted):
        if self.drone_id != event.drone_id:
            return
        if self.state != MissionState.WAITING_FOR_ACK:
            logger.warning("[SEARCH] Move started, but we are not waiting for the ack.")
            return

        self.state = MissionState.FLYING

    async def handle_move_executed(self, event: MoveExecuted):
        if self.drone_id != event.drone_id:
            return
        if self.state != MissionState.FLYING:
            logger.warning("[SEARCH] Move executed, but we are not in FLYING state.")
            return

        self.moves_performed += 1

        self.state = MissionState.WAITING_FOR_DRONE
        await self.event_bus.publish(GetPhotoAndTelemetryCommand(drone_id=self.drone_id))

    async def handle_drone_connection_lost(self, event: DroneConnectionLost):
        if self.drone_id != event.drone_id:
            return
        if self.state == MissionState.PAUSED:
            return
        # We only look at this two states, because when FLYING drone always sends info after executing command.
        # If drone cannot send the command (broken connection) sends it repeatedly as long as no ACK is received.
        if self.state == MissionState.WAITING_FOR_DRONE:
            self.pending_command = GetPhotoAndTelemetryCommand(drone_id=self.drone_id)
            self.state = MissionState.PAUSED
        elif self.state == MissionState.WAITING_FOR_ACK:
            self.pending_command = ExecuteMoveCommand(drone_id=self.drone_id, move=self.current_move)
            self.state = MissionState.PAUSED

    async def handle_drone_reconnected(self, event: DroneReconnected):
        if self.drone_id != event.drone_id:
            return
        if self.state != MissionState.PAUSED:
            return

        if isinstance(self.pending_command, GetPhotoAndTelemetryCommand):
            self.state = MissionState.WAITING_FOR_DRONE
        elif isinstance(self.pending_command, ExecuteMoveCommand):
            self.state = MissionState.WAITING_FOR_ACK

        if self.pending_command is not None:
            await self.event_bus.publish(self.pending_command)
            self.pending_command = None

    async def handle_drone_disconnected(self, event: DroneDisconnected):
        if self.drone_id != event.drone_id:
            return

        logger.info("[SEARCH] Drone disconnected. Ending mission.")

        await self._terminate_mission(found=False)

    async def handle_drone_error(self, event: DroneErrorOccurred):
        if self.drone_id != event.drone_id:
            return

        await self._abort_mission(f"Drone Connection/Hardware Error: {event.error_message}")

    async def handle_vlm_error(self, event: VlmErrorOccurred):
        if self.mission_id != event.chat_id:
            return

        await self._abort_mission(f"VLM Analysis Error: {event.error_message}")

    async def handle_chat_error(self, event: ChatErrorOccurred):
        if self.mission_id != event.chat_id:
            return

        await self._abort_mission(f"Chat/Storage Error: {event.error_message}")

    async def _abort_mission(self, reason: str):
        """ Aborts mission after critical error. """
        if self.state == MissionState.ENDED:
            return

        logger.error(f"[SEARCH] Mission {self.mission_id} aborted. Reason: {reason}")

        await self._terminate_mission(found=False, error_message=reason)

    async def _terminate_mission(self, found: bool, error_message: str | None = None):
        if self.state == MissionState.ENDED:
            return

        self.state = MissionState.ENDED
        self._cleanup()
        await self.event_bus.publish(StopRecordingCommand(drone_id=self.drone_id))

        await self.event_bus.publish(
            SearchEnded(
                mission_id=self.mission_id,
                found=found,
                moves_performed=self.moves_performed,
                error_message=error_message
            )
        )

        await self.event_bus.publish(DeleteSessionCommand(chat_id=self.mission_id))

    def _cleanup(self):
        self.event_bus.unsubscribe(PhotoWithTelemetryReceived, self.handle_photo_and_telemetry)
        self.event_bus.unsubscribe(VlmAnalysisCompleted, self.handle_vlm_analysis)
        self.event_bus.unsubscribe(UserDecisionReceived, self.handle_user_decision)
        self.event_bus.unsubscribe(MoveExecuted, self.handle_move_executed)
        self.event_bus.unsubscribe(MoveStarted, self.handle_move_started)
        self.event_bus.unsubscribe(DroneConnectionLost, self.handle_drone_connection_lost)
        self.event_bus.unsubscribe(DroneReconnected, self.handle_drone_reconnected)
        self.event_bus.unsubscribe(DroneDisconnected, self.handle_drone_disconnected)

        self.event_bus.unsubscribe(DroneErrorOccurred, self.handle_drone_error)
        self.event_bus.unsubscribe(VlmErrorOccurred, self.handle_vlm_error)
        self.event_bus.unsubscribe(ChatErrorOccurred, self.handle_chat_error)
