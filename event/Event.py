from abc import ABC
from enum import IntEnum
from typing import Optional


class EventType(IntEnum):
    """
    Events type to subscribe to and dispatch.

    Events:
    - ACTIVE_SCREEN_CHANGED: Dispatched when the active screen changes.
    """

    ACTIVE_SCREEN_CHANGED = 1


class Event(ABC):
    """
    Base event class.
    """
    def to_dict(self):
        raise NotImplementedError

class MouseEvent(Event):
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


class CommandEvent(Event):
    """
    Command event data structure.
    """

    def __init__(self, command: str, params: Optional[dict] = None):
        self.command = command
        self.params = params if params else {}

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "params": self.params
        }

