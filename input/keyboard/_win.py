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

        self.caps_lock_state = False

    def _key_event_action(self, event: KeyboardEvent):
        key = KeyUtilities.map_key(event.key)

        if event.action == KeyboardEvent.PRESS_ACTION:
            # Handl Caps Lock toggle
            if key == Key.caps_lock:
                if self.caps_lock_state:
                    self._controller.release(key)
                else:
                    self._controller.press(key)
                self.caps_lock_state = not self.caps_lock_state
            elif KeyUtilities.is_special(key):  # General special key handling
                if key in self.pressed_keys:
                    self._controller.release(key)
                    self.pressed_keys.discard(key)

            # Press key
            self._controller.press(key)
            self.pressed_keys.add(key)
        elif event.action == KeyboardEvent.RELEASE_ACTION:
            self._controller.release(key)
            self.pressed_keys.discard(key)  # We don't need to check cause discard doesn't raise