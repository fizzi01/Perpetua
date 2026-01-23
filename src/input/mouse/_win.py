"""
Provides mouse input support for Windows systems.
"""

from . import _base


class ServerMouseListener(_base.ServerMouseListener):
    """
    It listens for mouse events on Windows systems.
    Its main purpose is to capture mouse movements and clicks. And handle some border cases like cursor reaching screen edges.
    """

    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5

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
                self._listener._suppress = True  #ty: ignore
                return False
            else:
                self._listener._suppress = False  #ty: ignore
        else:
            self._listener._suppress = False  #ty: ignore

        return True


class ServerMouseController(_base.ServerMouseController):
    """
    It controls mouse events on Windows systems.
    Its main purpose is to move the mouse cursor and perform clicks.
    """

    pass


class ClientMouseController(_base.ClientMouseController):
    """
    It controls mouse events on Windows systems.
    Its main purpose is to move the mouse cursor and perform clicks.
    """

    pass
