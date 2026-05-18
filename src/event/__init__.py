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


class BusEventType(IntEnum):
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

    # Dispatched when a client's workspace placements change at runtime
    # (e.g. the GUI saves a new layout via SetClientLayout). Lets the
    # mouse listener refresh its cached EdgeBindings without forcing the
    # client to reconnect.
    CLIENT_LAYOUT_UPDATED = 8

    # Dispatched on the CLIENT side when the server pushes a fresh
    # topology (reverse edge bindings + server bbox). Used by the
    # client mouse controller to resolve return-to-server crossings
    # spatially instead of via the legacy ScreenPosition enum.
    CLIENT_TOPOLOGY_UPDATED = 9


class BusEvent(ABC):
    """
    Base class for events dispatched on the EventBus.

    ``__slots__ = ()`` on the base lets concrete subclasses opt into real
    slot-based instances (no per-event ``__dict__`` allocation) without
    fighting the ABC metaclass.
    """

    __slots__ = ()

    def to_dict(self):
        raise NotImplementedError


class ClientStreamReconnectedEvent(BusEvent):
    """
    Event dispatched when a client stream reconnects.
    """

    def __init__(self, client_uid: str, streams: list[int]):
        self.client_uid = client_uid
        self.streams = streams

    def to_dict(self) -> dict:
        return {"client_uid": self.client_uid, "stream_id": self.streams}


class ActiveScreenChangedEvent(BusEvent):
    """
    Event dispatched when the active client changes.

    ``active_screen`` now carries the active client's **UID** (or
    ``None`` to mean "back to server"). The field name is preserved
    for historical reasons but its contract changed in the UID-routing
    migration — never set it to a ``ScreenPosition`` string.
    """

    def __init__(
        self,
        active_screen: Optional[str],
        source: str = "",
        position: tuple[float, float] = (-1, -1),
    ):
        """
        Represents a change in the active client.

        Args:
            active_screen: Optional[str]
                UID of the active client; ``None`` means "back to server".
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

    ``client_uid`` is the client's stable identifier (mirrored from
    :attr:`model.client.ClientObj.uid`) and is the routing key used by
    the mouse listener, stream handlers, and cursor worker.

    ``edge_bindings`` carries the spatial cross-screen contract derived
    from the client's effective placements (real or synthesized from
    the legacy ``screen_position``) and the server's monitor list. Each
    entry is a serialized :class:`utils.screen.EdgeBinding` and carries
    both server-side and client-side axis info — the same record drives
    forward routing on the server AND return-to-server routing on the
    client (pushed via the ``CLIENT_TOPOLOGY`` command). Empty only
    when the client has no placement and no legacy position to anchor
    to (e.g. ``screen_position == "center"``).
    """

    def __init__(
        self,
        client_uid: str,
        streams: Optional[list[int]] = None,
        edge_bindings: Optional[list[dict]] = None,
    ):
        self.client_uid = client_uid
        self.streams = streams
        self.edge_bindings: list[dict] = list(edge_bindings) if edge_bindings else []

    def to_dict(self) -> dict:
        return {
            "client_uid": self.client_uid,
            "streams": self.streams,
            "edge_bindings": list(self.edge_bindings),
        }


class ClientDisconnectedEvent(ClientConnectedEvent):
    """
    Event dispatched when a client disconnects.
    """

    pass


class ClientTopologyUpdatedEvent(BusEvent):
    """Dispatched on the CLIENT after the server pushes a topology
    update. Carries the unified edge bindings (the client reads the
    ``client_*`` fields of each entry) plus the server's virtual bbox
    so the client can normalize the return-to-server cursor position
    over it.
    """

    def __init__(
        self,
        edge_bindings: Optional[list[dict]] = None,
        server_bbox: Optional[tuple[int, int, int, int]] = None,
    ):
        self.edge_bindings: list[dict] = (
            list(edge_bindings) if edge_bindings else []
        )
        self.server_bbox = server_bbox

    def to_dict(self) -> dict:
        return {
            "edge_bindings": list(self.edge_bindings),
            "server_bbox": list(self.server_bbox) if self.server_bbox else None,
        }


class ClientLayoutUpdatedEvent(BusEvent):
    """Dispatched when a client's workspace placements are mutated at
    runtime (typically from the GUI's layout editor). Carries the
    refreshed serialized EdgeBindings so the mouse listener can hot-swap
    its routing cache (and push the new topology to the client) without
    forcing it to disconnect.
    """

    def __init__(
        self,
        client_uid: str,
        edge_bindings: Optional[list[dict]] = None,
    ):
        self.client_uid = client_uid
        self.edge_bindings: list[dict] = list(edge_bindings) if edge_bindings else []

    def to_dict(self) -> dict:
        return {
            "client_uid": self.client_uid,
            "edge_bindings": list(self.edge_bindings),
        }


class ClientActiveEvent(BusEvent):
    """
    Event dispatched on the CLIENT side when the server activates it.

    ``client_uid`` is the client's own UID (echoed by the server, used
    for log correlation on the client side).

    ``client_monitor_id`` (optional) tells the receiving client which of
    its own monitors the server's cursor crossed into. When set, the
    client mouse controller denormalizes incoming positions against
    that monitor's bbox instead of the full virtual desktop, so the
    cursor lands on the right physical screen on multi-monitor clients.
    """

    def __init__(
        self,
        client_uid: str,
        client_monitor_id: Optional[int] = None,
    ):
        self.client_uid = client_uid
        self.client_monitor_id = client_monitor_id

    def to_dict(self) -> dict:
        return {
            "client_uid": self.client_uid,
            "client_monitor_id": self.client_monitor_id,
        }


class Event(ABC):
    """
    Base event class.

    ``__slots__ = ()`` lets concrete subclasses (e.g. :class:`MouseEvent`)
    opt into real slot-based instances — no per-event ``__dict__`` alloc on
    a hot path that creates 1000s of events per second under heavy mouse use.
    """

    __slots__ = ()

    def to_dict(self):
        raise NotImplementedError


class MouseEvent(Event):
    """
    Mouse event data structure.

    Slot-based: each event is the hottest allocation in the project (one per
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

    Slot-based for the same reason as :class:`MouseEvent`: high allocation
    rate when keys are held / repeated.
    """

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
    """
    Command event data structure.
    """

    CROSS_SCREEN = "cross_screen"
    FORCE_SCREEN_CHANGE = "force_screen_change"
    KEYBOARD_STATE_SYNC = "keyboard_state_sync"
    CLIENT_TOPOLOGY = "client_topology"

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

    ``client_monitor_id`` carries the target monitor on the receiving
    client when the server's spatial routing has matched an
    :class:`EdgeBinding`. ``None`` falls back to the client's virtual
    desktop bbox (legacy single-monitor behaviour).
    """

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
    """Command event sent from server to client to push a fresh
    topology. The client's :class:`command.CommandHandler` translates
    this into a :class:`ClientTopologyUpdatedEvent` on the bus, which
    the mouse controller listens to.

    ``edge_bindings`` is a list of :class:`utils.screen.EdgeBinding`
    dicts — the client reads the ``client_*`` fields to find the local
    edge / axis range that returns to the server. ``server_bbox`` is
    a 4-tuple ``(min_x, min_y, max_x, max_y)`` of the server's virtual
    desktop, used to normalise the return-to-server cursor position.
    """

    def __init__(
        self,
        source: str = "",
        target: str = "",
        edge_bindings: Optional[list[dict]] = None,
        server_bbox: Optional[tuple[int, int, int, int]] = None,
    ):
        super().__init__(
            command=CommandEvent.CLIENT_TOPOLOGY,
            source=source,
            target=target,
            params={
                "edge_bindings": list(edge_bindings) if edge_bindings else [],
                "server_bbox": list(server_bbox) if server_bbox else None,
            },
        )

    def get_edge_bindings(self) -> list[dict]:
        return list(self.params.get("edge_bindings") or [])

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
        )

    def to_dict(self) -> dict:
        bbox = self.params.get("server_bbox")
        return {
            "command": self.command,
            "params": {
                "edge_bindings": list(self.params.get("edge_bindings") or []),
                "server_bbox": list(bbox) if bbox else None,
            },
        }


class ForceScreenChangeCommandEvent(CommandEvent):
    """
    Force screen change command event data structure.
    """

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
    """
    Keyboard state sync command event data structure.
    """

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
