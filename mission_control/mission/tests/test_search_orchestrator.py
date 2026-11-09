import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from mission_control.core.action_status import ActionStatus
from mission_control.core.events import (
    AnalyzePhotoCommand,
    AskUserConfirmationCommand,
    MoveExecuted,
    PhotoWithTelemetryReceived,
    StartMissionCommand,
    UserDecisionReceived,
    VlmAnalysisCompleted,
)
from mission_control.mission.search_orchestrator import SearchOrchestrator


def published(bus, event_type):
    return [c.args[0] for c in bus.publish.call_args_list if isinstance(c.args[0], event_type)]


class Mission:
    def __init__(self, tmp_path, glimpses=10):
        self.tmp_path = tmp_path
        self.bus = MagicMock()
        self.bus.publish = AsyncMock()
        prompts = MagicMock()
        prompts.generate_prompt = AsyncMock(return_value="system prompt")
        self.orchestrator = SearchOrchestrator(self.bus, prompts)
        self.photos = 0
        asyncio.run(
            self.orchestrator.start(
                StartMissionCommand(
                    mission_id="m",
                    drone_id="d",
                    prompt_type="RL",
                    prompt_args={
                        "object": "helipad",
                        "glimpses": glimpses,
                        "area": 80,
                        "minimum_altitude": 10,
                        "maximum_altitude": 120,
                    },
                )
            )
        )

    def photo(self, altitude):
        self.photos += 1
        telemetry = self.tmp_path / f"t{self.photos}.json"
        telemetry.write_text(json.dumps({"data": {"position": {"alt": altitude}}}))
        asyncio.run(
            self.orchestrator.handle_photo_and_telemetry(
                PhotoWithTelemetryReceived(drone_id="d", photo_path=self.tmp_path / "p.jpg", telemetry_path=telemetry)
            )
        )
        return published(self.bus, AnalyzePhotoCommand)[-1]

    def model_moves(self, move):
        asyncio.run(
            self.orchestrator.handle_vlm_analysis(
                VlmAnalysisCompleted(chat_id="m", reasoning="", move=move, found=False)
            )
        )
        return published(self.bus, AskUserConfirmationCommand)[-1].move

    def operator(self, decision, move=None):
        asyncio.run(
            self.orchestrator.handle_user_decision(UserDecisionReceived(mission_id="m", decision=decision, move=move))
        )

    def executed(self):
        self.orchestrator.state = self.orchestrator.state.FLYING
        asyncio.run(self.orchestrator.handle_move_executed(MoveExecuted(drone_id="d", ok=True)))


def test_first_observation_uses_the_training_initial_format(tmp_path):
    mission = Mission(tmp_path)

    command = mission.photo(30.0)

    assert command.initial
    assert command.observation_text == (
        "\nTask: Find helipad\n"
        "Current position: (0.0, 0.0, 30.0)\n"
        "Map bounds: X [-40.0, 40.0] Y [-40.0, 40.0] Alt [10.0, 120.0] m\n"
        "Max steps: 10\n"
        "Use move(dx, dy, dz) to navigate (right/forward/up relative to the camera view, in meters). "
        "Use found() when directly above the target at low altitude."
    )


def test_step_observation_tracks_position_and_step(tmp_path):
    mission = Mission(tmp_path)
    mission.photo(30.0)
    move = mission.model_moves((5.0, -3.0, -10.0))
    mission.operator(ActionStatus.CONFIRMED, move)
    mission.executed()

    command = mission.photo(20.0)

    assert not command.initial
    assert command.observation_text == (
        "Movement successful.\n"
        "Position: (5.0, -3.0, 20.0)\n"
        "Step: 1/10\n"
        "Map bounds: X [-40.0, 40.0] Y [-40.0, 40.0] Alt [10.0, 120.0] m"
    )


def test_descent_below_minimum_altitude_stops_at_the_minimum(tmp_path):
    mission = Mission(tmp_path)
    mission.photo(30.0)

    move = mission.model_moves((1.0, 2.0, -25.0))
    mission.operator(ActionStatus.CONFIRMED, move)
    mission.executed()

    assert move == (1.0, 2.0, -20.0)
    assert mission.photo(10.0).observation_text.startswith("Movement limited: Altitude limit (10.0m – 120.0m)\n")


def test_descent_at_minimum_altitude_is_blocked(tmp_path):
    mission = Mission(tmp_path)
    mission.photo(10.0)

    assert mission.model_moves((0.0, 0.0, -5.0)) == (0.0, 0.0, 0.0)


def test_climb_above_maximum_altitude_stops_at_the_maximum(tmp_path):
    mission = Mission(tmp_path)
    mission.photo(100.0)

    assert mission.model_moves((0.0, 0.0, 50.0)) == (0.0, 0.0, 20.0)


def test_operator_warning_is_reported_as_a_blocked_move(tmp_path):
    mission = Mission(tmp_path)
    mission.photo(30.0)
    mission.model_moves((10.0, 0.0, 0.0))
    mission.operator(ActionStatus.WARNING)

    command = mission.photo(30.0)

    assert command.observation_text.startswith("Movement failed: Obstacle collision\nPosition: (0.0, 0.0, 30.0)\nStep: 0/10")
