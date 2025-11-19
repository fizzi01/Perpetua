from abc import ABC
from enum import IntEnum
from time import time
from typing import Optional

from network.protocol.message import ProtocolMessage, MessageType


class EventType(IntEnum):
    """
    Events type to subscribe to and dispatch.

    Events:
    - ACTIVE_SCREEN_CHANGED: Dispatched when the active screen changes.
    """

    ACTIVE_SCREEN_CHANGED = 1
    CLIENT_CONNECTED = 4
    CLIENT_DISCONNECTED = 5
    CLIENT_ACTIVE = 2
    CLIENT_INACTIVE = 3


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

    MOVE_ACTION = "move"
    POSITION_ACTION = "position"
    CLICK_ACTION = "click"
    RCLICK_ACTION = "rclick"
    SCROLL_ACTION = "scroll"

    def __init__(self, x: float = -1, y: float = -1, dx: float = 0, dy: float = 0, button: Optional[int] = None, action: Optional[str] = None, is_presed: bool = False):
        self.x = x
        self.y = y
        self.dx = dx
        self.dy = dy
        self.button = button
        self.action = action

        self.is_pressed = is_presed
        self.timestamp = time()

    # When passing mouse event data as function parameter it should be converted to dictionary
    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "dx": self.dx,
            "dy": self.dy,
            "button": self.button,
            "event": self.action,
            "is_pressed": self.is_pressed
        }


class CommandEvent(Event):
    """
    Command event data structure.
    """

    CROSS_SCREEN = "cross_screen"

    def __init__(self, command: str, source: str = "", target: str = "", params: Optional[dict] = None):
        self.command = command
        self.source = source    # Only when receiving commands
        self.target = target    # Only when receiving commands
        self.params = params if params else {}

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "params": self.params
        }


class ScreenEvent(Event):
    """
    Screen event data structure.
    """

    def __init__(self, data: dict):
        self.data = data # It should contain information about client cursor position

    def to_dict(self) -> dict:
        return {
            "data": self.data
        }


class EventMapper:
    """
    Maps protocol messages to event objects.
    """

    @staticmethod
    def get_event(message: ProtocolMessage) -> Optional[Event]:
        event_type = message.message_type
        message_payload = message.payload

        if event_type == MessageType.MOUSE:
            return MouseEvent(
                x=message_payload.get("x"),
                y=message_payload.get("y"),
                dx=message_payload.get("dx"),
                dy=message_payload.get("dy"),
                button=message_payload.get("button"),
                action=message_payload.get("event"),
                is_presed=message_payload.get("is_pressed", False)
            )
        elif event_type == MessageType.COMMAND:
            return CommandEvent(
                source=message.source,
                target=message.target,
                command=message_payload.get("command"),
                params=message_payload.get("params", {})
            )
        else:
            return None
