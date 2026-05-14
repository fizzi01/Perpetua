#  Perpetua - open-source and cross-platform KVM software.
#  Copyright (c) 2026 Federico Izzi.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

from abc import ABC
import asyncio
from typing import Callable, Dict, List, Optional, Any, TypeVar
import inspect

from utils import BackgroundTasks
from utils.logging import get_logger

from . import BusEvent

T = TypeVar("T", bound=BusEvent)


class EventBus(ABC):
    """
    Async event dispatching system that allows registration of event listeners and dispatching events to them.
    """

    def subscribe(
        self,
        event_type: int,
        callback: Callable[[Optional[T]], Any],
        priority: bool = False,
    ):
        """
        Subscribe a callback function to a specific event type.
        """

    def unsubscribe(self, event_type: int, callback: Callable[[Optional[T]], Any]):
        """
        Unsubscribe a callback function from a specific event type.
        """

    async def dispatch(self, event_type: int, data: Optional[T] = None, **kwargs):
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

        # Loop is captured lazily: the bus may be constructed before the loop
        # is running (e.g. unit-test fixtures). dispatch_nowait grabs it on first call.
        try:
            self._loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._bg = BackgroundTasks()

    def subscribe(
        self,
        event_type: int,
        callback: Callable[[Optional[T]], Any],
        priority: bool = False,
    ):
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

    def unsubscribe(self, event_type: int, callback: Callable[[Optional[T]], Any]):
        """
        Unsubscribe a callback function from an event type.
        """
        if (
            event_type in self._subscribers
            and callback in self._subscribers[event_type]
        ):
            self._subscribers[event_type].remove(callback)

    async def dispatch(self, event_type: int, data: Optional[T] = None, **kwargs):
        """
        Async dispatch of an event to all registered listeners.
        Executes all callbacks concurrently for maximum performance.
        Supports both sync and async callbacks.
        """
        # Fast path: get listeners without lock (dict access is atomic in CPython)
        listeners = tuple(self._subscribers.get(event_type, ()))

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
        Safe to call from non-loop threads (native input callbacks).
        Drops the event with a warning if no loop is running.
        """
        coro = self.dispatch(event_type, *args, **kwargs)
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
                self._loop = loop
            except RuntimeError:
                coro.close()
                self._logger.warning(
                    f"dispatch_nowait dropped: no running loop (event_type={event_type})"
                )
                return
        if loop.is_closed():
            coro.close()
            self._logger.warning(
                f"dispatch_nowait dropped: loop is closed (event_type={event_type})"
            )
            return
        try:
            loop.call_soon_threadsafe(self._bg.spawn, coro)
        except RuntimeError as e:
            coro.close()
            self._logger.warning(
                f"dispatch_nowait dropped (event_type={event_type}): {e}"
            )

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
                await loop.run_in_executor(None, lambda: callback(data, **kwargs))  # type: ignore
        except Exception as e:
            # import traceback

            self._logger.error(f"Exception raised while dispatching event ({e})")
            # self._logger.error(traceback.format_exc())


# Backward compatibility alias
class ThreadSafeEventBus(EventBus):
    """
    Backward compatibility alias for AsyncEventBus.
    """

    pass
