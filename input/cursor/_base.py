from multiprocessing import Queue, Pipe
from typing import Optional

from event import EventType
from event.EventBus import EventBus
from network.stream.GenericStream import StreamHandler


# Abstract cursor base class

class CursorHandlerWorker(object):
    """
    A utility class for handling cursor visibility on macOS.
    """

    def __init__(self, event_bus: EventBus, stream: Optional[StreamHandler] = None, debug: bool = False):
        """
        Initializes the CursorHandlerWorker.

        Args:
            event_bus (EventBus): The event bus for handling events.
            stream (Optional[StreamHandler]): The trasmit mouse movement data.
            debug (bool): Flag to enable debug mode.
        """
        self.event_bus = event_bus
        self.stream = stream

        self._debug = debug

        self.command_queue = Queue()
        self.result_queue = Queue()

        # Unidirectional pipe for mouse movement
        self.mouse_conn_rec, self.mouse_conn_send = Pipe(duplex=False)

        self.process = None
        self.is_running = False
        self._moue_data_thread = None

        # Register to active_screen
        self.event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED, callback=self._on_active_screen_changed)
        self.event_bus.subscribe(event_type=EventType.CLIENT_ACTIVE, callback=self._on_client_active)
        self.event_bus.subscribe(event_type=EventType.CLIENT_INACTIVE, callback=self._on_client_inactive)

    def _on_active_screen_changed(self, event):
        pass

    def _on_client_active(self, event):
        pass

    def _on_client_inactive(self, event):
        pass

    def start(self, wait_ready=True, timeout=1):
        raise NotImplementedError()

    def stop(self, timeout=2):
        raise NotImplementedError()