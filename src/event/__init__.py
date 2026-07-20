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
from typing import Optional, Self, TYPE_CHECKING

from network.protocol.message import ProtocolMessage, MessageType

if TYPE_CHECKING:
    # Keep ``ScreenEdge`` out of the runtime import graph to preserve
    # the event-layer / input-layer separation.
    from input.utils import ScreenEdge


class BusEventType(IntEnum):
    """Events to subscribe to and dispatch on the bus."""

    ACTIVE_SCREEN_CHANGED = 1
    SCREEN_CHANGE_GUARD = 6

    CLIENT_CONNECTED = 4
    CLIENT_DISCONNECTED = 5

    CLIENT_ACTIVE = 2
    CLIENT_INACTIVE = 3

    CLIENT_STREAM_RECONNECTED = 7

    SCREEN_SWITCH_DIRECTIONAL_REQUEST = 10
    SCREEN_SWITCH_CYCLE_REQUEST = 11

    # Dispatched when a client's workspace placements change at runtime
    # (e.g. the GUI saves a new layout). Lets the listener refresh its
    # cached EdgeBindings without forcing the client to reconnect.
    CLIENT_LAYOUT_UPDATED = 8

    # Dispatched on the CLIENT side when the server pushes a fresh
    # topology (reverse edge bindings + server bbox).
    CLIENT_TOPOLOGY_UPDATED = 9

    # Dispatched on the SERVER side when a connected client reports its
    # monitor list changed at runtime. Triggers placement reconciliation
    # against the new monitor ids.
    CLIENT_MONITORS_UPDATED = 12

    # Dispatched by whichever local watch loop is running (server or
    # client) when the LOCAL OS monitor signature changes. Lets the mouse
    # layer re-read ``Screen.get_monitor_layout()`` / ``get_virtual_bbox()``
    # so cached geometry doesn't go stale after a hotplug. No payload:
    # handlers re-read ``Screen`` directly (``data=None``).
    LOCAL_MONITORS_UPDATED = 13


class BusEvent(ABC):
    """Base class for events dispatched on the EventBus."""

    __slots__ = ()

    def to_dict(self):
        raise NotImplementedError


class ClientStreamReconnectedEvent(BusEvent):
    """Event dispatched when a client stream reconnects."""

    def __init__(self, client_uid: str, streams: list[int]):
        self.client_uid = client_uid
        self.streams = streams

    def to_dict(self) -> dict:
        return {"client_uid": self.client_uid, "stream_id": self.streams}


class ActiveScreenChangedEvent(BusEvent):
    """Event dispatched when the active client changes.

    ``active_screen`` carries the active client's UID, or ``None`` for
    "back to server". Never set it to a ``ScreenPosition`` string.
    """

    def __init__(
        self,
        active_screen: Optional[str],
        source: str = "",
        position: tuple[float, float] = (-1, -1),
    ):
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


class ScreenSwitchDirectionalRequestEvent(BusEvent):
    """Dispatched when the user presses a directional spatial hotkey."""

    def __init__(self, edge: "ScreenEdge"):
        super().__init__()
        self.edge = edge

    def to_dict(self) -> dict:
        # ``.name`` keeps the payload bus-instrumentation friendly
        # without leaking the enum type across the layer boundary.
        return {"edge": getattr(self.edge, "name", str(self.edge))}


class ScreenSwitchCycleRequestEvent(BusEvent):
    """Dispatched when the user presses the screen cycle hotkey."""

    def __init__(self, direction: int):
        super().__init__()
        self.direction = direction

    def to_dict(self) -> dict:
        return {"direction": self.direction}


class ClientConnectedEvent(BusEvent):
    """Event dispatched when a new client connects.

    ``edge_bindings`` carries the spatial cross-screen contract derived
    from the client's effective placements (real or synthesized from
    the legacy ``screen_position``) and the server's monitor list. The
    same record drives forward routing on the server AND return-to-
    server routing on the client (pushed via the ``CLIENT_TOPOLOGY``
    command).
    """

    def __init__(
        self,
        client_uid: str,
        streams: Optional[list[int]] = None,
        edge_bindings: Optional[list[dict]] = None,
        intra_client_bindings: Optional[list[dict]] = None,
    ):
        self.client_uid = client_uid
        self.streams = streams
        self.edge_bindings: list[dict] = list(edge_bindings) if edge_bindings else []
        self.intra_client_bindings: list[dict] = (
            list(intra_client_bindings) if intra_client_bindings else []
        )

    def to_dict(self) -> dict:
        return {
            "client_uid": self.client_uid,
            "streams": self.streams,
            "edge_bindings": list(self.edge_bindings),
            "intra_client_bindings": list(self.intra_client_bindings),
        }


class ClientDisconnectedEvent(ClientConnectedEvent):
    """Event dispatched when a client disconnects."""

    pass


class ClientTopologyUpdatedEvent(BusEvent):
    """Topology pushed by the server: edge bindings + server virtual bbox."""

    def __init__(
        self,
        edge_bindings: Optional[list[dict]] = None,
        server_bbox: Optional[tuple[int, int, int, int]] = None,
        intra_client_bindings: Optional[list[dict]] = None,
    ):
        self.edge_bindings: list[dict] = list(edge_bindings) if edge_bindings else []
        self.server_bbox = server_bbox
        self.intra_client_bindings: list[dict] = (
            list(intra_client_bindings) if intra_client_bindings else []
        )

    def to_dict(self) -> dict:
        return {
            "edge_bindings": list(self.edge_bindings),
            "server_bbox": list(self.server_bbox) if self.server_bbox else None,
            "intra_client_bindings": list(self.intra_client_bindings),
        }


class ClientLayoutUpdatedEvent(BusEvent):
    """Refreshed edge bindings after the admin edits a client's placements."""

    def __init__(
        self,
        client_uid: str,
        edge_bindings: Optional[list[dict]] = None,
        intra_client_bindings: Optional[list[dict]] = None,
    ):
        self.client_uid = client_uid
        self.edge_bindings: list[dict] = list(edge_bindings) if edge_bindings else []
        self.intra_client_bindings: list[dict] = (
            list(intra_client_bindings) if intra_client_bindings else []
        )

    def to_dict(self) -> dict:
        return {
            "client_uid": self.client_uid,
            "edge_bindings": list(self.edge_bindings),
            "intra_client_bindings": list(self.intra_client_bindings),
        }


class ClientMonitorsUpdatedEvent(BusEvent):
    """Client-reported monitor list change; raw dicts pending reconciliation."""

    def __init__(
        self,
        client_uid: str,
        monitors: Optional[list[dict]] = None,
    ):
        self.client_uid = client_uid
        self.monitors: list[dict] = list(monitors) if monitors else []

    def to_dict(self) -> dict:
        return {
            "client_uid": self.client_uid,
            "monitors": list(self.monitors),
        }


class ClientActiveEvent(BusEvent):
    """Event dispatched on the CLIENT side when the server activates it.

    ``client_monitor_id`` (optional) selects which of the client's own
    monitors the cursor lands on. ``position_x`` / ``position_y`` carry
    landing coords on the SAME packet that flips ``_is_active``, so they
    can't race the activation against a parallel ``POSITION_ACTION`` on
    the mouse stream (which would get dropped by the ``_is_active``
    gate and leave the cursor at the previous session's position).
    ``-1`` means "no explicit landing requested" (legacy / hotkey path).
    """

    def __init__(
        self,
        client_uid: str,
        client_monitor_id: Optional[int] = None,
        position_x: float = -1,
        position_y: float = -1,
    ):
        self.client_uid = client_uid
        self.client_monitor_id = client_monitor_id
        self.position_x = position_x
        self.position_y = position_y

    def to_dict(self) -> dict:
        return {
            "client_uid": self.client_uid,
            "client_monitor_id": self.client_monitor_id,
            "position_x": self.position_x,
            "position_y": self.position_y,
        }


class Event(ABC):
    """Base event class. Slot-based so subclasses can avoid per-instance dicts."""

    __slots__ = ()

    def to_dict(self):
        raise NotImplementedError


class MouseEvent(Event):
    """Mouse event data structure.

    Slot-based: this is the hottest allocation in the project (one per
    mouse move on a fast pointer = thousands per second).
    """

    __slots__ = ("x", "y", "dx", "dy", "button", "action", "is_pressed", "timestamp")

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
        is_pressed: bool = False,
    ):
        self.x = x
        self.y = y
        self.dx = dx
        self.dy = dy
        self.button = button
        self.action = action
        self.is_pressed = is_pressed
        self.timestamp = time()

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
    """Keyboard event data structure. Slot-based; allocated on every key event."""

    __slots__ = ("key", "action", "timestamp")

    PRESS_ACTION = "press"
    RELEASE_ACTION = "release"

    def __init__(self, key: str, action: str):
        self.key = key
        self.action = action
        self.timestamp = time()

    def to_dict(self) -> dict:
        return {"key": self.key, "event": self.action}


class CommandEvent(Event):
    """Command event data structure."""

    CROSS_SCREEN = "cross_screen"
    FORCE_SCREEN_CHANGE = "force_screen_change"
    KEYBOARD_STATE_SYNC = "keyboard_state_sync"
    CLIENT_TOPOLOGY = "client_topology"
    CLIENT_MONITORS_UPDATE = "client_monitors_update"

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
    """Cross-screen command; ``client_monitor_id=None`` falls back to client bbox."""

    def __init__(
        self,
        source: str = "",
        target: str = "",
        x: float | int = -1,
        y: float | int = -1,
        client_monitor_id: Optional[int] = None,
    ):
        super().__init__(
            command=CommandEvent.CROSS_SCREEN,
            source=source,
            target=target,
            params={
                "x": x,
                "y": y,
                "client_monitor_id": client_monitor_id,
            },
        )

    def get_position(self) -> tuple[float | int, float | int]:
        return self.params.get("x", -1), self.params.get("y", -1)

    def get_client_monitor_id(self) -> Optional[int]:
        v = self.params.get("client_monitor_id")
        return int(v) if v is not None else None

    @classmethod
    def from_command_event(cls, event: CommandEvent) -> Self:
        cm_raw = event.params.get("client_monitor_id")
        return cls(
            source=event.source,
            target=event.target,
            x=event.params.get("x", -1),
            y=event.params.get("y", -1),
            client_monitor_id=int(cm_raw) if cm_raw is not None else None,
        )

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "params": {
                "x": self.params.get("x", -1),
                "y": self.params.get("y", -1),
                "client_monitor_id": self.params.get("client_monitor_id"),
            },
        }


class ClientTopologyCommandEvent(CommandEvent):
    """Server-to-client topology push; mapped to ``ClientTopologyUpdatedEvent``."""

    def __init__(
        self,
        source: str = "",
        target: str = "",
        edge_bindings: Optional[list[dict]] = None,
        server_bbox: Optional[tuple[int, int, int, int]] = None,
        intra_client_bindings: Optional[list[dict]] = None,
    ):
        super().__init__(
            command=CommandEvent.CLIENT_TOPOLOGY,
            source=source,
            target=target,
            params={
                "edge_bindings": list(edge_bindings) if edge_bindings else [],
                "server_bbox": list(server_bbox) if server_bbox else None,
                "intra_client_bindings": (
                    list(intra_client_bindings) if intra_client_bindings else []
                ),
            },
        )

    def get_edge_bindings(self) -> list[dict]:
        return list(self.params.get("edge_bindings") or [])

    def get_intra_client_bindings(self) -> list[dict]:
        return list(self.params.get("intra_client_bindings") or [])

    def get_server_bbox(self) -> Optional[tuple[int, int, int, int]]:
        raw = self.params.get("server_bbox")
        if not raw or len(raw) != 4:
            return None
        return (int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3]))

    @classmethod
    def from_command_event(cls, event: CommandEvent) -> Self:
        raw_bbox = event.params.get("server_bbox")
        return cls(
            source=event.source,
            target=event.target,
            edge_bindings=event.params.get("edge_bindings") or [],
            server_bbox=tuple(raw_bbox) if raw_bbox and len(raw_bbox) == 4 else None,
            intra_client_bindings=event.params.get("intra_client_bindings") or [],
        )

    def to_dict(self) -> dict:
        bbox = self.params.get("server_bbox")
        return {
            "command": self.command,
            "params": {
                "edge_bindings": list(self.params.get("edge_bindings") or []),
                "server_bbox": list(bbox) if bbox else None,
                "intra_client_bindings": list(
                    self.params.get("intra_client_bindings") or []
                ),
            },
        }


class ClientMonitorsUpdateCommandEvent(CommandEvent):
    """Client-to-server monitor list change; ``client_uid`` routes to the right ClientObj."""

    def __init__(
        self,
        source: str = "",
        target: str = "server",
        client_uid: str = "",
        monitors: Optional[list[dict]] = None,
    ):
        super().__init__(
            command=CommandEvent.CLIENT_MONITORS_UPDATE,
            source=source,
            target=target,
            params={
                "client_uid": client_uid,
                "monitors": list(monitors) if monitors else [],
            },
        )

    def get_client_uid(self) -> str:
        return str(self.params.get("client_uid", ""))

    def get_monitors(self) -> list[dict]:
        return list(self.params.get("monitors") or [])

    @classmethod
    def from_command_event(cls, event: CommandEvent) -> Self:
        return cls(
            source=event.source,
            target=event.target,
            client_uid=str(event.params.get("client_uid", "")),
            monitors=list(event.params.get("monitors") or []),
        )

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "params": {
                "client_uid": self.params.get("client_uid", ""),
                "monitors": list(self.params.get("monitors") or []),
            },
        }


class ForceScreenChangeCommandEvent(CommandEvent):
    """Force screen change command event."""

    def __init__(self, source: str = "", target: str = ""):
        super().__init__(
            command=CommandEvent.FORCE_SCREEN_CHANGE,
            source=source,
            target=target,
            params={"force": True},
        )

    @classmethod
    def from_command_event(cls, event: CommandEvent) -> Self:
        return cls(source=event.source, target=event.target)

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "params": {"force": True},
        }


class KeyboardStateSyncCommandEvent(CommandEvent):
    """Keyboard state sync command event."""

    def __init__(
        self,
        source: str = "",
        target: str = "",
        pressed_keys: Optional[list[str]] = None,
    ):
        super().__init__(
            command=CommandEvent.KEYBOARD_STATE_SYNC,
            source=source,
            target=target,
            params={"pressed_keys": pressed_keys if pressed_keys else []},
        )

    def get_pressed_keys(self) -> list[str]:
        return self.params.get("pressed_keys", [])

    @classmethod
    def from_command_event(cls, event: CommandEvent) -> Self:
        return cls(
            source=event.source,
            target=event.target,
            pressed_keys=event.params.get("pressed_keys", []),
        )

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "params": {"pressed_keys": self.params.get("pressed_keys", [])},
        }


class ScreenEvent(Event):
    """Screen event data structure."""

    def __init__(self, data: dict):
        self.data = data

    def to_dict(self) -> dict:
        return {"data": self.data}


class ClipboardEvent(Event):
    """Clipboard event data structure."""

    def __init__(self, content: str | None, content_type: str = "text"):
        self.content = content
        self.content_type = content_type
        self.timestamp = time()

    def to_dict(self) -> dict:
        return {"content": self.content, "content_type": self.content_type}


class EventMapper:
    """Maps protocol messages to event objects."""

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
                is_pressed=message_payload.get("is_pressed", False),
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
