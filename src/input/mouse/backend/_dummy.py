from pynput.mouse import Button


class MouseListener:
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


class MouseController:
    """Dummy controller"""

    def __init__(self):
        self._position = (-1, -1)

    def move(self, *args, **kwargs):
        # No operation
        return None

    def position(self, *args, **kwargs):
        # Return dummy position
        return None

    def press(self, *args, **kwargs):
        # No operation
        return None

    def release(self, *args, **kwargs):
        # No operation
        return None

    def click(self, *args, **kwargs):
        # No operation
        return None

    def scroll(self, *args, **kwargs):
        # No operation
        return None

__all__ = ["MouseListener", "MouseController", "Button"]