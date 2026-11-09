from enum import Enum, auto

from mission_control.core.action_status import ActionStatus
from mission_control.core.events import (
    AnalyzePhotoCommand,
    AskUserConfirmationCommand,
    ChatErrorOccurred,
    CreateNewSessionCommand,
    DeleteSessionCommand,
    DroneConnectionLost,
    DroneDisconnected,
    DroneErrorOccurred,
    DroneReconnected,
    ExecuteMoveCommand,
    GetPhotoAndTelemetryCommand,
    MoveExecuted,
    MoveStarted,
    PhotoWithTelemetryReceived,
    SaveSessionCommand,
    SearchEnded,
    StartMissionCommand,
    StartRecordingCommand,
    StopRecordingCommand,
    UserDecisionReceived,
    VlmAnalysisCompleted,
    VlmErrorOccurred,
)
from mission_control.ai.prompt_helpers.training_prompt import (
    altitude_limit_reason,
    format_bounds,
    format_initial_observation,
    format_move_feedback,
    format_position,
    format_step_observation,
)
from mission_control.core.interfaces import EventBus, PromptHelper
from mission_control.utils.logger import get_configured_logger
from mission_control.utils.parsers import get_height_async

logger = get_configured_logger(__name__)


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
    def __init__(
        self, event_bus: EventBus, prompts: PromptHelper, *, video_enabled: bool = False
    ):
        self.pending_command = None
        self.moves_performed = 0
        self.event_bus = event_bus
        self.video_enabled = video_enabled
        self.mission_id: str = ""
        self.initial_prompt: str = ""
        self.drone_id: str = ""
        self.state: MissionState = MissionState.IDLE
        self.max_moves: int = 0
        self.prompt_helper = prompts
        self.current_move = None
        self.move_failures = 0

        # Mission parameters shown to the model in each observation (training format).
        self.task_hint: str = ""
        self.half_area: float = 40.0
        self.min_altitude: float = 10.0
        self.max_altitude: float = 120.0
        # Horizontal position relative to the start point, in the camera frame
        # (x right, y toward the top of the image), summed from executed moves.
        self.position_xy: tuple[float, float] = (0.0, 0.0)
        self.last_altitude: float | None = None
        self.observation_sent: bool = False
        # Feedback line for the next observation, in the training env wording.
        self.feedback: str = format_move_feedback(True, "", True)
        self.pending_feedback: str = self.feedback

        self.event_bus.subscribe(
            PhotoWithTelemetryReceived, self.handle_photo_and_telemetry
        )
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
            self.task_hint = f"Find {kv.get('object', 'helipad')}"
            self.half_area = float(kv.get("area", 80)) / 2
            self.min_altitude = float(kv.get("minimum_altitude", 10))
            self.max_altitude = float(kv.get("maximum_altitude", 120))

            self.max_moves = glimpses - 1
            if self.max_moves == -1:
                raise ValueError("[SEARCH] No glimpses attribute or glimpses set to 0.")

            kv["mission_id"] = self.mission_id
            self.initial_prompt = await self.prompt_helper.generate_prompt(kind, kv)

            await self.event_bus.publish(
                CreateNewSessionCommand(
                    chat_id=self.mission_id, prompt=self.initial_prompt
                ),
                wait_for_completion=True,
            )
            await self.event_bus.publish(
                SaveSessionCommand(chat_id=self.mission_id), wait_for_completion=True
            )
            if self.state == MissionState.ENDED:
                return
            self.state = MissionState.WAITING_FOR_DRONE
            if self.video_enabled:
                await self.event_bus.publish(
                    StartRecordingCommand(drone_id=self.drone_id),
                    wait_for_completion=True,
                )
            await self.event_bus.publish(
                GetPhotoAndTelemetryCommand(drone_id=self.drone_id)
            )
        except Exception as e:
            await self._abort_mission(reason=str(e))
            raise

    async def handle_photo_and_telemetry(self, event: PhotoWithTelemetryReceived):
        if self.drone_id != event.drone_id:
            return
        if self.state != MissionState.WAITING_FOR_DRONE:
            logger.warning(
                "[SEARCH] Photo with telemetry received, but we are not waiting for the drone."
            )
            return

        self.last_altitude = float(await get_height_async(event.telemetry_path))
        command = AnalyzePhotoCommand(
            chat_id=self.mission_id,
            photo_path=event.photo_path,
            telemetry_path=event.telemetry_path,
            observation_text=self._observation_text(),
            initial=not self.observation_sent,
        )
        self.observation_sent = True
        self.state = MissionState.WAITING_FOR_VLM
        await self.event_bus.publish(command)

    def _observation_text(self) -> str:
        # In training, "Max steps" counts tool calls: the model may call found() after
        # max_steps - 1 moves, which matches glimpses = max_moves + 1 here.
        max_steps = self.max_moves + 1
        position = format_position(*self.position_xy, self.last_altitude)
        bounds = format_bounds(self.half_area, self.min_altitude, self.max_altitude)
        if not self.observation_sent:
            return format_initial_observation(self.task_hint, position, bounds, max_steps)
        return format_step_observation(self.feedback, position, self.moves_performed, max_steps, bounds)

    async def handle_vlm_analysis(self, event: VlmAnalysisCompleted):
        if self.mission_id != event.chat_id:
            return
        if self.state != MissionState.WAITING_FOR_VLM:
            logger.warning(
                "[SEARCH] VLM analysis received, but we are not waiting for the vlm."
            )
            return

        await self.event_bus.publish(SaveSessionCommand(chat_id=self.mission_id))

        if event.found:
            await self._terminate_mission(found=True)
            return

        if self.moves_performed >= self.max_moves:
            await self._terminate_mission(found=False)
            return

        move = self._clamp_to_altitude_limits(event.move)
        command = AskUserConfirmationCommand(
            mission_id=self.mission_id, reasoning=event.reasoning, move=move
        )
        self.state = MissionState.WAITING_FOR_USER
        await self.event_bus.publish(command)

    async def handle_user_decision(self, event: UserDecisionReceived):
        if self.mission_id != event.mission_id:
            return
        if self.state != MissionState.WAITING_FOR_USER:
            logger.warning(
                "[SEARCH] User decision received, but we are not waiting for the user."
            )
            return

        if event.decision == ActionStatus.CANCELLED:
            await self._terminate_mission(found=False)
            return

        if event.decision == ActionStatus.WARNING:
            # The operator rejected the move as unsafe; reported like a blocked move in training.
            self.feedback = format_move_feedback(False, "Obstacle collision", False)
            self.state = MissionState.WAITING_FOR_DRONE
            await self.event_bus.publish(
                GetPhotoAndTelemetryCommand(drone_id=self.drone_id)
            )

        if event.decision == ActionStatus.CONFIRMED:
            self.current_move = event.move
            self.move_failures = 0
            command = ExecuteMoveCommand(drone_id=self.drone_id, move=event.move)
            self.state = MissionState.WAITING_FOR_ACK
            await self.event_bus.publish(command)

    def _clamp_to_altitude_limits(self, move):
        """The VLM sometimes ignores the altitude limits, so a vertical move that would
        end outside them is shortened to stop exactly at the limit. The model is told
        about it in the next observation, the same way the training env reports it."""
        self.pending_feedback = format_move_feedback(True, "", True)
        if move is None or self.last_altitude is None:
            return move

        x, y, z = move
        altitude = self.last_altitude
        target = altitude + z
        if self.min_altitude <= target <= self.max_altitude:
            return move

        if target < self.min_altitude:
            clamped_z = min(0.0, self.min_altitude - altitude)
        else:
            clamped_z = max(0.0, self.max_altitude - altitude)
        logger.warning(
            f"[SEARCH] Move {move} would end at {target:.1f}m, outside the altitude limits "
            f"[{self.min_altitude:.1f}m, {self.max_altitude:.1f}m]. Clamping z to {clamped_z:.1f}."
        )
        clamped = (x, y, clamped_z)
        reason = altitude_limit_reason(self.min_altitude, self.max_altitude)
        self.pending_feedback = format_move_feedback(False, reason, any(clamped))
        return clamped

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

        if not getattr(event, "ok", True):
            self.move_failures += 1
            if self.move_failures >= 3:
                await self._abort_mission("Move failed 3 times consecutively.")
            else:
                logger.warning(f"[SEARCH] Move failed (attempt {self.move_failures}). Retrying...")
                self.state = MissionState.WAITING_FOR_ACK
                command = ExecuteMoveCommand(drone_id=self.drone_id, move=self.current_move)
                await self.event_bus.publish(command)
            return

        self.move_failures = 0
        self.moves_performed += 1
        x, y = self.position_xy
        self.position_xy = (x + self.current_move[0], y + self.current_move[1])
        self.feedback = self.pending_feedback

        self.state = MissionState.WAITING_FOR_DRONE
        await self.event_bus.publish(
            GetPhotoAndTelemetryCommand(drone_id=self.drone_id)
        )

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
            self.pending_command = ExecuteMoveCommand(
                drone_id=self.drone_id, move=self.current_move
            )
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

        await self._abort_mission(
            f"Drone Connection/Hardware Error: {event.error_message}"
        )

    async def handle_vlm_error(self, event: VlmErrorOccurred):
        if self.mission_id != event.chat_id:
            return

        await self._abort_mission(f"VLM Analysis Error: {event.error_message}")

    async def handle_chat_error(self, event: ChatErrorOccurred):
        if self.mission_id != event.chat_id:
            return

        await self._abort_mission(f"Chat/Storage Error: {event.error_message}")

    async def _abort_mission(self, reason: str):
        """Aborts mission after critical error."""
        if self.state == MissionState.ENDED:
            return

        logger.error(f"[SEARCH] Mission {self.mission_id} aborted. Reason: {reason}")

        await self._terminate_mission(found=False, error_message=reason)

    async def _terminate_mission(self, found: bool, error_message: str | None = None):
        if self.state == MissionState.ENDED:
            return

        self.state = MissionState.ENDED
        self._cleanup()
        if self.video_enabled:
            await self.event_bus.publish(StopRecordingCommand(drone_id=self.drone_id))

        await self.event_bus.publish(
            SearchEnded(
                mission_id=self.mission_id,
                found=found,
                moves_performed=self.moves_performed,
                error_message=error_message,
            )
        )

        await self.event_bus.publish(DeleteSessionCommand(chat_id=self.mission_id))

    def _cleanup(self):
        self.event_bus.unsubscribe(
            PhotoWithTelemetryReceived, self.handle_photo_and_telemetry
        )
        self.event_bus.unsubscribe(VlmAnalysisCompleted, self.handle_vlm_analysis)
        self.event_bus.unsubscribe(UserDecisionReceived, self.handle_user_decision)
        self.event_bus.unsubscribe(MoveExecuted, self.handle_move_executed)
        self.event_bus.unsubscribe(MoveStarted, self.handle_move_started)
        self.event_bus.unsubscribe(
            DroneConnectionLost, self.handle_drone_connection_lost
        )
        self.event_bus.unsubscribe(DroneReconnected, self.handle_drone_reconnected)
        self.event_bus.unsubscribe(DroneDisconnected, self.handle_drone_disconnected)

        self.event_bus.unsubscribe(DroneErrorOccurred, self.handle_drone_error)
        self.event_bus.unsubscribe(VlmErrorOccurred, self.handle_vlm_error)
        self.event_bus.unsubscribe(ChatErrorOccurred, self.handle_chat_error)
