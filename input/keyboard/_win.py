from event import KeyboardEvent
from event.EventBus import EventBus
from network.stream.GenericStream import StreamHandler

from pynput.keyboard import Key, KeyCode
from ._base import BaseServerKeyboardListener, BaseClientKeyboardController, KeyUtilities


class ServerKeyboardListener(BaseServerKeyboardListener):
    def __init__(self, event_bus: EventBus, stream_handler: StreamHandler, command_stream: StreamHandler,
                 filtering: bool = True):
        super().__init__(event_bus, stream_handler, command_stream, filtering)

    def _win32_suppress_filter(self, msg, data):
        self._listener._suppress = self._listening


class ClientKeyboardController(BaseClientKeyboardController):
    def __init__(self, event_bus: EventBus, stream_handler: StreamHandler, command_stream: StreamHandler):
        super().__init__(event_bus, stream_handler, command_stream)