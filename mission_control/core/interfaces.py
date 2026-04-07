from typing import Protocol, Callable, Type, Any, Awaitable, List, Dict

from mission_control.conversation.abstract_conversation import Conversation
from mission_control.core.events import (
    AnalyzePhotoCommand,
    CreateNewSessionCommand,
    DeleteSessionCommand,
    SaveSessionCommand,
    LoadSessionCommand
)

class EventBus(Protocol):
    """
    Abstract interface for the event bus (Message Broker / Event Bus).
    All domain components should depend on this interface rather
    than a concrete implementation (e.g., MemoryEventBus).
    """

    def subscribe(self, event_type: Type, handler: Callable[[Any], Awaitable[None]]) -> None:
        """Registers an asynchronous handler for a given event type."""
        ...

    def unsubscribe(self, event_type: Type, handler: Callable[[Any], Awaitable[None]]) -> None:
        """Unregisters a handler for a given event type."""
        ...

    async def publish(self, event: Any, wait_for_completion: bool = False) -> None:
        """Publishes an event to all registered subscribers."""
        ...

class ChatStorageHelper(Protocol):
    """
    Abstract interface for classes managing the persistence (saving and loading)
    of the chat history with the VLM model.
    """

    async def save_chat(self, chat_id: str, conversation: Conversation) -> None:
        """
        Serializes and saves the current chat history to persistent storage.
        """
        ...

    async def load_chat(self, chat_id: str) -> List[Dict[str, Any]]:
        """
        Reconstructs and returns a conversation object based on the saved chat identifier.
        """
        ...

class PromptHelper(Protocol):
    """
    Abstract interface for a service that generates and manages
    system prompts for the Vision Language Model (VLM).
    """

    async def generate_prompt(self, kind: str, args: Dict[str, str]) -> str:
        """
        Generates the system prompt text based on the mission kind and parameters.

        Args:
            kind: The type of prompt (e.g., "FS-1", "FS-2").
            args: A dictionary of parameters (e.g., {"object": "person", "glimpses": "5"}).

        Returns:
            The ready-to-use prompt string to be sent to the model.
        """
        ...

class VLMBridge(Protocol):
    """
    Abstract interface for the Vision Language Model (VLM) Service.
    It acts as an event-driven adapter, managing chat sessions and
    processing analysis requests from the orchestrator.
    """

    async def handle_analyze_photo(self, event: AnalyzePhotoCommand) -> None:
        """
        Prepares the context (image, telemetry) and queries the VLM.
        Publishes the analysis result or an error event.
        """
        ...

    async def handle_create_new_session(self, event: CreateNewSessionCommand) -> None:
        """
        Initializes a new chat session with a specific system prompt.
        """
        ...

    async def handle_delete_session(self, event: DeleteSessionCommand) -> None:
        """
        Removes an active chat session from the service's memory.
        """
        ...

    async def handle_save_session(self, event: SaveSessionCommand) -> None:
        """
        Triggers the storage helper to persist the current chat state to disk/database.
        """
        ...

    async def handle_load_session(self, event: LoadSessionCommand) -> None:
        """
        Restores a chat session from persistent storage into the service's memory.
        """
        ...
