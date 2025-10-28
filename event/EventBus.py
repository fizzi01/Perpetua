from abc import ABC
from typing import Callable, Dict, List

from threading import Lock

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

    def dispatch(self, event_type: int, *args, **kwargs):
        """
        Thread-safe dispatching of an event to all registered listeners.
        """
        with self._lock:
            listeners = self._subscribers.get(event_type, []).copy()

        for callback in listeners:
            try:
                callback(*args, **kwargs)
            except Exception as e:
                # Handle or log the exception as needed
                self.logger.exception("Exception raised while dispatching event: %s", e)
