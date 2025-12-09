"""
Provides mouse input support for Windows systems.
"""
from ._base import ServerMouseListener, ClientMouseController, ServerMouseController

class ServerMouseListener(ServerMouseListener):
    """
    It listens for mouse events on Windows systems.
    Its main purpose is to capture mouse movements and clicks. And handle some border cases like cursor reaching screen edges.
    """
    def _win32_mouse_suppress_filter(self, msg, data):
        """
        Suppress mouse events when listening.
        """
        if self._listening:
            # msg = 513/514 -> left down/up
            # msg = 516/517 -> right down/up
            # msg = 519/520 -> middle down/up
            # msg = 522/523 -> scroll
            if msg in (513, 514, 516, 517, 519, 520, 522, 523):
                self._listener._suppress = True
                return False
            else:
                self._listener._suppress = False
        else:
            self._listener._suppress = False

        return True

class ServerMouseController(ServerMouseController):
    """
    It controls mouse events on Windows systems.
    Its main purpose is to move the mouse cursor and perform clicks.
    """
    pass

class ClientMouseController(ClientMouseController):
    """
    It controls mouse events on Windows systems.
    Its main purpose is to move the mouse cursor and perform clicks.
    """
    pass