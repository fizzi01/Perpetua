from pynput.keyboard import Key, KeyCode


class KeyboardListener:
    """Dummy listener"""

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        self._running = False

    def start(self):
        # Dummy: do not spawn threads, keep not running
        self._running = False

    def stop(self):
        self._running = False

    def is_alive(self) -> bool:
        return False


class KeyboardController:
    """Dummy controller"""

    def press(self, key):
        # No operation
        return None

    def release(self, key):
        # No operation
        return None


__all__ = ["KeyboardListener", "KeyboardController", "Key", "KeyCode"]
