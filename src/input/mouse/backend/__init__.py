from sys import platform
from os import environ

# Import platform-specific mouse backends
if platform.startswith("linux"):
    # Check if we're running under Wayland or Xorg
    if environ.get("XDG_SESSION_TYPE") == "wayland":
        # Wayland backend (not implemented yet)
        raise NotImplementedError("Wayland backend is not implemented yet")
    else:
        from ._xorg import MouseListener
        from pynput.mouse import Controller as MouseController
else:
    from pynput.mouse import Listener as MouseListener
    from pynput.mouse import Controller as MouseController

from pynput.mouse import Button

__all__ = ["MouseListener", "MouseController", "Button"]