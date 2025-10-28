from enum import IntEnum
from typing import Optional


class EventType(IntEnum):
    """
    Events type to subscribe to and dispatch.

    Events:
    - ACTIVE_SCREEN_CHANGED: Dispatched when the active screen changes.
    """

    ACTIVE_SCREEN_CHANGED = 1


class MouseEvent:
    """
    Mouse event data structure.
    """

    def __init__(self, x: int, y: int, button: Optional[int] = None, action: Optional[str] = None, is_presed: bool = False):
        self.x = x
        self.y = y
        self.button = button
        self.action = action

        self.is_pressed = is_presed

    # When passing mouse event data as function parameter it should be converted to dictionary
    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "button": self.button,
            "event": self.action,
            "is_pressed": self.is_pressed
        }