from abc import ABC
from typing import Callable, Dict, List

from threading import Lock, Thread

from utils.logging.logger import Logger


class EventBus(ABC):
    """
    Event dispatching system that allows registration of event listeners and dispatching events to them.
    """

    def subscribe(self, event_type: int, callback: Callable):
        """
        Subscribe a callback function to a specific event type.
        """

    def unsubscribe(self, event_type: int, callback: Callable):
        """
        Unsubscribe a callback function from a specific event type.
        """

    def dispatch(self, event_type: int, *args, **kwargs):
        """
        Dispatch an event to all registered listeners for the given event type.
        """

    def async_dispatch(self, event_type: int, *args, **kwargs):
        """
        Asynchronously dispatch an event to all registered listeners for the given event type.
        """


class ThreadSafeEventBus(EventBus):
    """
    Thread-safe implementation of the EventBus.
    """
    def __init__(self):
        super().__init__()
        # Initialize thread-safe structures here
        self._subscribers: Dict[int, List[Callable]] = {}
        self._lock = Lock()

        self.logger = Logger.get_instance()

    def subscribe(self, event_type: int, callback: Callable):
        """
        Thread-safe subscription to an event type.
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: int, callback: Callable):
        """
        Thread-safe unsubscription from an event type.
        """
        with self._lock:
            if event_type in self._subscribers:
                self._subscribers[event_type].remove(callback)

    def dispatch(self, event_type: int, workers: int = 0, blocking: bool = False, timeout: float = 0, *args, **kwargs):
        """
        Thread-safe dispatching of an event to all registered listeners.
        If workers > 0, callbacks are executed in parallel threads.
        """
        with self._lock:
            listeners = self._subscribers.get(event_type, []).copy()

        if not listeners:
            return

        if workers > 1:
            # Execute callbacks in parallel using threads
            threads = []
            for callback in listeners:
                thread = Thread(target=self._safe_callback, args=(callback, *args), kwargs=kwargs)
                thread.start()
                threads.append(thread)

            # Wait for all threads to complete
            if blocking:
                for thread in threads:
                    thread.join(timeout=timeout)
        else:
            # Execute callbacks sequentially
            for callback in listeners:
                self._safe_callback(callback, *args, **kwargs)

    def _safe_callback(self, callback: Callable, *args, **kwargs):
        """
        Execute a callback with exception handling.
        """
        try:
            callback(*args, **kwargs)
        except Exception as e:
            self.logger.exception("Exception raised while dispatching event: %s", e)

    def async_dispatch(self, event_type: int, workers: int = 0, blocking: bool = False, timeout: float = 0, *args, **kwargs):
        """
        Asynchronously dispatch an event to all registered listeners for the given event type.
        """
        thread = Thread(target=self.dispatch, args=(event_type, *args), kwargs=kwargs)
        thread.start()
