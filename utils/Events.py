import logging
import threading
import time
from collections import deque
from typing import Callable, Dict, List, Any

from utils.Interfaces import IEventBus


class EventBus(IEventBus):

    def __init__(self):
        super().__init__()

        logging.basicConfig(level=logging.ERROR, format="[%(levelname)s][EVENTBUS] %(message)s")

        self.subscribers: Dict[str, List[Callable]] = {}
        self._queue = deque()
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._running = False
        self._dispatcher_thread = None

    def subscribe(self, event_type: str, handler: Callable):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)

    def publish(self, event: str, *args, **kwargs):
        # Inserimento in coda molto rapido
        with self._condition:
            self._queue.append((event, args, kwargs))
            # Notifichiamo immediatamente l'arrivo dell'evento
            self._condition.notify()
            time.sleep(0.0001)

    def start(self):
        self._running = True
        self._dispatcher_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatcher_thread.start()

    def join(self, timeout: int = 0):
        with self._condition:
            self._running = False
            self._condition.notify_all()
            time.sleep(0.0001)

        if self._dispatcher_thread is not None:
            self._dispatcher_thread.join()

    def _dispatch_loop(self):
        while True:
            with self._condition:
                while self._running and not self._queue:
                    self._condition.wait()  # Attende evento
                if not self._running:
                    break

                # BURST DISPATCH: estraggo TUTTI gli eventi in coda in un’unica passata
                events_to_process = []
                while self._queue:
                    events_to_process.append(self._queue.popleft())

                # Processiamo gli eventi fuori dal lock
            for event_type, args, kwargs in events_to_process:
                handlers = self.subscribers.get(event_type, [])
                for handler in handlers:
                    try:
                        handler(*args, **kwargs)
                    except TypeError as e:
                        logging.error(f"Error while dispatching event {event_type}\n Wrong handler signature:\n {e}")
