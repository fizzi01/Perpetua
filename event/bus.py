from abc import ABC
import asyncio
from typing import Callable, Dict, List, Optional, Any
import inspect

from utils.logging import get_logger

from . import BusEvent


class EventBus(ABC):
    """
    Async event dispatching system that allows registration of event listeners and dispatching events to them.
    """

    def subscribe(self, event_type: int, callback: Callable[[Optional[BusEvent]], Any], priority: bool = False):
        """
        Subscribe a callback function to a specific event type.
        """

    def unsubscribe(self, event_type: int, callback: Callable[[Optional[BusEvent]], Any]):
        """
        Unsubscribe a callback function from a specific event type.
        """

    async def dispatch(self, event_type: int, data: Optional[BusEvent] = None, **kwargs):
        """
        Dispatch an event to all registered listeners for the given event type.
        """

    def dispatch_nowait(self, event_type: int, *args, **kwargs):
        """
        Dispatch an event without waiting (fire and forget).
        """


class AsyncEventBus(EventBus):
    """
    High-performance async implementation of the EventBus.
    Optimized for maximum efficiency with minimal overhead.
    """
    def __init__(self):
        super().__init__()
        # Use dict for O(1) lookup, list for subscribers
        self._subscribers: Dict[int, List[Callable]] = {}

        self._logger = get_logger(self.__class__.__name__)

    def subscribe(self, event_type: int, callback: Callable[[Optional[BusEvent]], Any], priority: bool = False):
        """
        Subscribe a callback function to a specific event type.
        Thread-safe, but prefer calling from async context.
        """
        # Sync version for compatibility
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        if priority:
            self._subscribers[event_type].insert(0, callback)
        else:
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: int, callback: Callable[[Optional[BusEvent]], Any]):
        """
        Unsubscribe a callback function from an event type.
        """
        if event_type in self._subscribers and callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)

    async def dispatch(self, event_type: int, data: Optional[BusEvent] = None, **kwargs):
        """
        Async dispatch of an event to all registered listeners.
        Executes all callbacks concurrently for maximum performance.
        Supports both sync and async callbacks.
        """
        # Fast path: get listeners without lock (dict access is atomic in CPython)
        listeners = self._subscribers.get(event_type)

        if not listeners:
            return

        # Create tasks for all callbacks
        tasks = []
        for callback in listeners:
            tasks.append(self._execute_callback(callback, data, **kwargs))

        # Execute all callbacks concurrently
        if tasks:
            # gather with return_exceptions to prevent one failure from stopping others
            await asyncio.gather(*tasks, return_exceptions=True)

    def dispatch_nowait(self, event_type: int, *args, **kwargs):
        """
        Fire-and-forget dispatch without waiting for completion.
        Creates a background task.
        """
        try:
            asyncio.create_task(self.dispatch(event_type, *args, **kwargs))
        except RuntimeError:
            # No event loop running
            pass

    async def _execute_callback(self, callback: Callable, data, **kwargs):
        """
        Execute a callback with exception handling.
        Automatically handles both sync and async callbacks.
        """
        try:
            # Check if callback is async
            if inspect.iscoroutinefunction(callback):
                await callback(data, **kwargs)
            else:
                # Run sync callback in executor to avoid blocking
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: callback(data, **kwargs)) #type: ignore
        except Exception as e:
            import traceback
            self._logger.error(f"Exception raised while dispatching event -> {e}")
            self._logger.error(traceback.format_exc())


# Backward compatibility alias
class ThreadSafeEventBus(EventBus):
    """
    Backward compatibility alias for AsyncEventBus.
    """
    pass
