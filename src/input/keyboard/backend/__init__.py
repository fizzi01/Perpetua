from sys import platform
from os import environ

# Import platform-specific mouse backends
if platform.startswith("linux"):
    # Check if we're running under Wayland or Xorg
    if environ.get("XDG_SESSION_TYPE") == "wayland":
        # Wayland backend (not implemented yet)
        raise NotImplementedError("Wayland backend is not implemented yet")
    else:
        from ._xorg import KeyboardListener, Key, KeyCode
        from pynput.keyboard import Controller as KeyboardController
else:
    from pynput.keyboard import Listener as KeyboardListener
    from pynput.keyboard import Controller as KeyboardController
    from pynput.keyboard import Key, KeyCode

__all__ = ["KeyboardListener", "KeyboardController", "Key", "KeyCode"]
