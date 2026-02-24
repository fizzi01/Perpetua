from sys import platform
from os import environ

MouseListener = None
MouseController = None
Button = None

# Import platform-specific mouse backends when available
if platform.startswith("linux"):
    # Check if we're running under Wayland or Xorg
    if (
        environ.get("XDG_SESSION_TYPE") == "wayland"
        or environ.get("WAYLAND_DISPLAY") is not None
    ):
        # Wayland backend (not implemented yet)
        print("Wayland backend not implemented yet, no mouse control available")
        from ._dummy import MouseListener, MouseController, Button

if not MouseListener or not MouseController or not Button:
    from pynput.mouse import Listener as MouseListener
    from pynput.mouse import Controller as MouseController
    from pynput.mouse import Button

__all__ = ["MouseListener", "MouseController", "Button"]
