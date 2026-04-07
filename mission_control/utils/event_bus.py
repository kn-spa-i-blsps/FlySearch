import asyncio
import logging
from collections import defaultdict
from typing import Callable, Dict, List, Type, Any, Awaitable, Set

from mission_control.core.interfaces import EventBus

logger = logging.getLogger(__name__)

class MemoryEventBus(EventBus):
    def __init__(self):
        self._subscribers: Dict[Type, List[Callable[[Any], Awaitable[None]]]] = defaultdict(list)
        self._background_tasks: Set[asyncio.Task] = set()

    def subscribe(self, event_type: Type, handler: Callable[[Any], Awaitable[None]]) -> None:
        if handler not in self._subscribers[event_type]:
            self._subscribers[event_type].append(handler)
            logger.debug(f"[EventBus] Registered {handler.__name__} for event {event_type.__name__}")

    def unsubscribe(self, event_type: Type, handler: Callable[[Any], Awaitable[None]]) -> None:
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)
            logger.debug(f"[EventBus] Unregistered {handler.__name__} for event {event_type.__name__}")

    async def publish(self, event: Any, wait_for_completion: bool = False) -> None:
        event_type = type(event)
        handlers = self._subscribers.get(event_type, [])

        if not handlers:
            logger.debug(f"[EventBus] No subscribers for event: {event_type.__name__}")
            return

        tasks = [asyncio.create_task(self._safe_execute(handler, event)) for handler in handlers]

        if wait_for_completion:
             await asyncio.gather(*tasks)
        else:
             # "Fire and forget"
             for task in tasks:
                 self._background_tasks.add(task)
                 task.add_done_callback(self._background_tasks.discard)

    @staticmethod
    async def _safe_execute(handler: Callable, event: Any):
        try:
            await handler(event)
        except Exception as e:
            logger.error(
                f"[EventBus] Error in {handler.__name__} during {type(event).__name__}: {e}",
                exc_info=True
            )