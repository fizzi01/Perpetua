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
from typing import Callable, Dict, List, Optional, Any, Tuple, TypeVar
import inspect

from utils import BackgroundTasks
from utils.logging import get_logger

from . import BusEvent

T = TypeVar("T", bound=BusEvent)

# Stored entry: (callback, is_coroutine_function). Pre-resolving the
# coroutine check at subscribe time keeps the per-event hot path branch-free.
_Subscriber = Tuple[Callable, bool]


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

    # Auto-disable a callback after this many consecutive failures so one buggy
    # listener can't keep poisoning every dispatch (logged loudly, easy to spot).
    MAX_CONSECUTIVE_FAILURES = 20

    def __init__(self):
        super().__init__()
        # Use dict for O(1) lookup, list for subscribers
        self._subscribers: Dict[int, List[_Subscriber]] = {}
        # (event_type, id(callback)) -> consecutive failure count
        self._failure_counts: Dict[Tuple[int, int], int] = {}

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
        Duplicate subscriptions are ignored (idempotent).
        Thread-safe, but prefer calling from async context.
        """
        subs = self._subscribers.get(event_type)
        if subs is None:
            subs = []
            self._subscribers[event_type] = subs
        else:
            # Skip duplicates: a second subscribe would otherwise double-fire
            # the callback on every event (e.g. on stream restart).
            for cb, _ in subs:
                if cb is callback or cb == callback:
                    return
        entry: _Subscriber = (callback, inspect.iscoroutinefunction(callback))
        if priority:
            subs.insert(0, entry)
        else:
            subs.append(entry)

    def unsubscribe(self, event_type: int, callback: Callable[[Optional[T]], Any]):
        """
        Unsubscribe a callback function from an event type.
        """
        subs = self._subscribers.get(event_type)
        if not subs:
            return
        for i, (cb, _) in enumerate(subs):
            if cb is callback or cb == callback:
                del subs[i]
                self._failure_counts.pop((event_type, id(cb)), None)
                return

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
        tasks = [
            self._execute_callback(event_type, cb, is_async, data, **kwargs)
            for cb, is_async in listeners
        ]

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

    async def _execute_callback(
        self,
        event_type: int,
        callback: Callable,
        is_async: bool,
        data,
        **kwargs,
    ):
        """
        Execute a callback with exception handling.
        `is_async` is pre-resolved at subscribe time to avoid an inspect call per event.
        A callback that keeps raising on consecutive events gets auto-unsubscribed
        after MAX_CONSECUTIVE_FAILURES to keep the bus healthy.
        """
        key = (event_type, id(callback))
        try:
            if is_async:
                await callback(data, **kwargs)
            else:
                # Run sync callback in executor to avoid blocking
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: callback(data, **kwargs))  # type: ignore
        except Exception as e:
            count = self._failure_counts.get(key, 0) + 1
            self._failure_counts[key] = count
            self._logger.error(
                f"Exception in event {event_type} callback "
                f"{getattr(callback, '__qualname__', repr(callback))} "
                f"({count}/{self.MAX_CONSECUTIVE_FAILURES}): {e}"
            )
            if count >= self.MAX_CONSECUTIVE_FAILURES:
                self._logger.warning(
                    f"Disabling callback "
                    f"{getattr(callback, '__qualname__', repr(callback))} "
                    f"for event {event_type}: too many consecutive failures"
                )
                self.unsubscribe(event_type, callback)
            return
        # Success: clear any prior failure streak so transient errors don't
        # accumulate toward the auto-disable threshold.
        if key in self._failure_counts:
            del self._failure_counts[key]


# Backward compatibility alias
class ThreadSafeEventBus(EventBus):
    """
    Backward compatibility alias for AsyncEventBus.
    """

    pass
