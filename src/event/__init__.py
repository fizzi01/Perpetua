#  Perpetua - open-source and cross-platform KVM software.
#  Copyright (c) 2026 Federico Izzi.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

from abc import ABC
from enum import IntEnum
from time import time
from typing import Optional, Self

from network.protocol.message import ProtocolMessage, MessageType


class EventType(IntEnum):
    """
    Events type to subscribe to and dispatch.

    Events:
    - ACTIVE_SCREEN_CHANGED: Dispatched when the active screen changes.
    - SCREEN_CHANGE_GUARD: Internal event to notify the cursor guard about screen changes.

    - CLIENT_CONNECTED: Dispatched when a new client connects.
    - CLIENT_DISCONNECTED: Dispatched when a client disconnects.
    - CLIENT_ACTIVE: Dispatched when the client becomes active.
    - CLIENT_INACTIVE: Dispatched when the client becomes inactive.
    """

    # Both uses ActiveScreenChangedEvent as data
    ACTIVE_SCREEN_CHANGED = (
        1  # Dispatched when the active screen effectively changes (after guard check)
    )
    SCREEN_CHANGE_GUARD = (
        6  # Internal event to notify the cursor guard about screen changes
    )

    CLIENT_CONNECTED = 4
    CLIENT_DISCONNECTED = 5

    # Client only events
    CLIENT_ACTIVE = 2
    CLIENT_INACTIVE = 3

    CLIENT_STREAM_RECONNECTED = 7  # Dispatched when a client stream reconnects


class BusEvent(ABC):
    """
    Base class for events dispatched on the EventBus.
    """

    def to_dict(self):
        raise NotImplementedError


class ClientStreamReconnectedEvent(BusEvent):
    """
    Event dispatched when a client stream reconnects.
    """

    def __init__(self, client_screen: str, streams: list[int]):
        self.client_screen = client_screen
        self.streams = streams

    def to_dict(self) -> dict:
        return {"client_screen": self.client_screen, "stream_id": self.streams}


class ActiveScreenChangedEvent(BusEvent):
    """
    Event dispatched when the active screen changes.
    """

    def __init__(
        self,
        active_screen: Optional[str],
        source: str = "",
        position: tuple[float, float] = (-1, -1),
    ):
        """
        Represents a change in the active screen (e.g., when a server crosses to another client's screen).

        Args:
            active_screen: Optional[str]
                Identifier for the active screen. Can be None if no active screen is set (so the server).
            source: str, optional
                Source information related to the object. Defaults to an empty string.
            position: tuple[float, float], optional
                A tuple defining the x and y coordinates of the object. Defaults to (-1.0, -1.0).
        """
        self.active_screen = active_screen
        self.client = source
        self.x = position[0]
        self.y = position[1]

    def to_dict(self) -> dict:
        return {
            "active_screen": self.active_screen,
            "client": self.client,
            "x": self.x,
            "y": self.y,
        }


class ClientConnectedEvent(BusEvent):
    """
    Event dispatched when a new client connects.
    """

    def __init__(self, client_screen: str, streams: Optional[list[int]] = None):
        self.client_screen = client_screen
        self.streams = streams

    def to_dict(self) -> dict:
        return {"client_screen": self.client_screen, "streams": self.streams}


class ClientDisconnectedEvent(ClientConnectedEvent):
    """
    Event dispatched when a client disconnects.
    """

    pass


class ClientActiveEvent(BusEvent):
    """
    Event dispatched when the client becomes active.
    """

    def __init__(self, client_screen: str):
        self.client_screen = client_screen

    def to_dict(self) -> dict:
        return {"client_screen": self.client_screen}


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

    def __init__(
        self,
        x: float = -1,
        y: float = -1,
        dx: float = 0,
        dy: float = 0,
        button: Optional[int] = None,
        action: Optional[str] = None,
        is_presed: bool = False,
    ):
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
            "is_pressed": self.is_pressed,
        }


class KeyboardEvent(Event):
    """
    Keyboard event data structure.
    """

    PRESS_ACTION = "press"
    RELEASE_ACTION = "release"

    def __init__(self, key: str, action: str):
        self.key = key
        self.action = action
        self.timestamp = time()

    def to_dict(self) -> dict:
        return {"key": self.key, "event": self.action}


class CommandEvent(Event):
    """
    Command event data structure.
    """

    CROSS_SCREEN = "cross_screen"

    def __init__(
        self,
        command: str,
        source: str | None = "",
        target: str | None = "",
        params: Optional[dict] = None,
    ):
        self.command = command
        # Only when receiving commands
        self.source = source if source else ""
        # Only when receiving commands
        self.target = target if target else ""
        self.params = params if params else {}

    @classmethod
    def from_command_event(cls, event: Self):
        pass

    def to_dict(self) -> dict:
        return {"command": self.command, "params": self.params}


class CrossScreenCommandEvent(CommandEvent):
    """
    Cross screen command event data structure.
    """

    def __init__(
        self,
        source: str = "",
        target: str = "",
        x: float | int = -1,
        y: float | int = -1,
    ):
        super().__init__(
            command=CommandEvent.CROSS_SCREEN,
            source=source,
            target=target,
            params={"x": x, "y": y},
        )

    def get_position(self) -> tuple[float | int, float | int]:
        return self.params.get("x", -1), self.params.get("y", -1)

    @classmethod
    def from_command_event(cls, event: CommandEvent) -> Self:
        return cls(
            source=event.source,
            target=event.target,
            x=event.params.get("x", -1),
            y=event.params.get("y", -1),
        )

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "params": {"x": self.params.get("x", -1), "y": self.params.get("y", -1)},
        }


class ScreenEvent(Event):
    """
    Screen event data structure.
    """

    def __init__(self, data: dict):
        self.data = data  # It should contain information about client cursor position

    def to_dict(self) -> dict:
        return {"data": self.data}


class ClipboardEvent(Event):
    """
    Clipboard event data structure.
    """

    def __init__(self, content: str | None, content_type: str = "text"):
        self.content = content
        self.content_type = content_type
        self.timestamp = time()

    def to_dict(self) -> dict:
        return {"content": self.content, "content_type": self.content_type}


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
                x=message_payload.get("x", -1),
                y=message_payload.get("y", -1),
                dx=message_payload.get("dx", 0),
                dy=message_payload.get("dy", 0),
                button=message_payload.get("button"),
                action=message_payload.get("event"),
                is_presed=message_payload.get("is_pressed", False),
            )
        elif event_type == MessageType.COMMAND:
            return CommandEvent(
                source=message.source,
                target=message.target,
                command=message_payload.get("command", ""),
                params=message_payload.get("params", {}),
            )
        elif event_type == MessageType.KEYBOARD:
            return KeyboardEvent(
                key=message_payload.get("key", ""),
                action=message_payload.get("event", ""),
            )
        elif event_type == MessageType.CLIPBOARD:
            return ClipboardEvent(
                content=message_payload.get("content", None),
                content_type=message_payload.get("content_type", "text"),
            )
        else:
            return None
