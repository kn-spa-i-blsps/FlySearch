from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mission_control.core.action_status import ActionStatus


# --- Base Classes ---

# kw_only=True forces keyword arguments during instantiation (e.g., Event(mission_id="123")).
# This prevents the "Non-default argument(s) follows default argument(s)" inheritance error.
@dataclass(kw_only=True)
class Message:
    """Base class for all messages passed through the Event Bus."""
    # Using timezone-aware datetime instead of the deprecated utcnow()
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: Optional[str] = None


@dataclass(kw_only=True)
class Event(Message):
    """Indicates that something has happened in the system."""
    pass


@dataclass(kw_only=True)
class Command(Message):
    """Instructs a bridge to perform a specific action."""
    pass


# --- 1. UI / Interface Events (from CLI & WEB) ---

@dataclass(kw_only=True)
class AskUserConfirmationCommand(Command):
    """TODO"""
    mission_id: str
    reasoning: str
    move: tuple


@dataclass(kw_only=True)
class MoveExecuted(Event):
    drone_id: str


@dataclass(kw_only=True)
class SearchEnded(Event):
    mission_id: str
    moves_performed: int
    found: bool
    error_message: str | None = None


@dataclass(kw_only=True)
class StartRecordingCommand(Command):
    drone_id: str


@dataclass(kw_only=True)
class StopRecordingCommand(Command):
    drone_id: str


@dataclass(kw_only=True)
class GetRecordingsListCommand(Command):
    drone_id: str


@dataclass(kw_only=True)
class PullRecordingsCommand(Command):
    drone_id: str
    names: List[str]


@dataclass(kw_only=True)
class RecordingsListReceived(Event):
    drone_id: str
    recordings: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass(kw_only=True)
class RecordingsPullCompleted(Event):
    drone_id: str
    results: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass(kw_only=True)
class UserDecisionReceived(Event):
    """
    Published by: CLI Handler / Web Server.
    Subscribed by: Search Saga Orchestrator.
    Contains the operator's decision (e.g., confirm target, abort search).
    """
    mission_id: str
    decision: ActionStatus
    move: tuple


# --- 2. DroneBridge Events ---

@dataclass(kw_only=True)
class PhotoWithTelemetryReceived(Event):
    """
    Published by: DroneBridge.
    Subscribed by: Search Saga Orchestrator.
    Triggered when a new photo and its associated telemetry arrive from the Physical Drone.
    """
    drone_id: str
    photo_path: Path
    telemetry_path: Path


@dataclass(kw_only=True)
class DroneConnectionLost(Event):
    """
    Published by: DroneBridge.
    Subscribed by: Search Saga Orchestrator.
    Emitted when the connection to the Physical Drone drops abnormally.
    """
    drone_id: str


@dataclass(kw_only=True)
class MoveStarted(Event):
    drone_id: str


@dataclass(kw_only=True)
class DroneDisconnected(Event):
    """
    Published by: DroneBridge.
    Subscribed by: Search Saga Orchestrator.
    Emitted when the connection to the Physical Drone drops normally.
    """
    drone_id: str


@dataclass(kw_only=True)
class DroneReconnected(Event):
    """TODO"""
    drone_id: str


@dataclass(kw_only=True)
class DroneErrorOccurred(Event):
    """
    Published by: DroneBridge.
    Subscribed by: Search Saga Orchestrator.
    Triggered if we cannot continue the communication.
    """
    drone_id: str
    error_message: str
    traceback: str | None = None


# --- 3. VLMBridge Events ---

@dataclass(kw_only=True)
class VlmAnalysisCompleted(Event):
    """
    Published by: VLMBridge.
    Subscribed by: Search Saga Orchestrator.
    Contains the result of the VLM Backend (Gemini/OpenAI) image analysis.
    """
    chat_id: str
    reasoning: str
    move: tuple
    found: bool


@dataclass(kw_only=True)
class VlmErrorOccurred(Event):
    """
    Published by: VLMBridge.
    Subscribed by: Search Saga Orchestrator.
    Triggered if we cannot continue the communication.
    """
    chat_id: str
    error_message: str
    traceback: str | None = None


@dataclass(kw_only=True)
class GetPhotoAndTelemetryCommand(Command):
    """
    Published by: Search Saga Orchestrator.
    Subscribed by: DroneBridge.
    Instructs the drone to take and transmit a photo with telemetry.
    """
    drone_id: str


# --- 4. Orchestrator Commands (Saga Actions) ---

@dataclass(kw_only=True)
class ExecuteMoveCommand(Command):
    """
    Published by: Search Saga Orchestrator.
    Subscribed by: DroneBridge.
    Instructs the drone to move specific vector in space.
    """
    drone_id: str
    move: tuple


@dataclass(kw_only=True)
class AnalyzePhotoCommand(Command):
    """
    Published by: Search Saga Orchestrator.
    Subscribed by: VLMBridge.
    Instructs the VLMBridge to analyze a specific set of messages/images.
    """
    chat_id: str
    is_warning: bool
    photo_path: Path
    telemetry_path: Path


@dataclass(kw_only=True)
class CreateNewSessionCommand(Command):
    """TODO"""
    chat_id: str
    prompt: str


@dataclass(kw_only=True)
class NewSessionCreated(Event):
    """TODO"""
    chat_id: str


@dataclass(kw_only=True)
class ChatErrorOccurred(Event):
    """TODO"""
    chat_id: str
    error_message: str
    traceback: str | None = None


@dataclass(kw_only=True)
class DeleteSessionCommand(Command):
    """TODO"""
    chat_id: str


@dataclass(kw_only=True)
class SessionDeleted(Event):
    """TODO"""
    chat_id: str


@dataclass(kw_only=True)
class SaveSessionCommand(Command):
    """TODO"""
    chat_id: str


@dataclass(kw_only=True)
class SessionSaved(Event):
    """TODO"""
    chat_id: str


@dataclass(kw_only=True)
class LoadSessionCommand(Command):
    """TODO"""
    chat_id: str


@dataclass(kw_only=True)
class SessionLoaded(Event):
    """TODO"""
    chat_id: str


@dataclass(kw_only=True)
class StartMissionCommand(Command):
    """TODO"""
    mission_id: str
    drone_id: str
    prompt_type: str
    prompt_args: Dict[str, Any]


# --- 5. State / System Events ---


@dataclass(kw_only=True)
class SystemShuttingDown(Event):
    """
    Published by: Main.
    Subscribed by: Everyone.
    Used to inform everyone to gracefully shutdown.
    """
