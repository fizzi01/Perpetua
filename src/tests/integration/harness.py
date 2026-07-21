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
"""In-process server<->client integration harness.

The harness wires the *real* server-side and client-side component graph
together without sockets or TLS. Each logical stream is represented by a
:class:`BridgeStreamHandler` that owns a real ``MessageExchange`` (so the
production msgpack framing + ``EventMapper`` decode run for every frame)
and delivers the resulting bytes straight to the peer side's exchange
dispatch. Only the OS-touching leaves are mocked:

* mouse/keyboard capture + injection (pynput ``Controller`` / ``Listener``);
* screen enumeration (``utils.screen.Screen``);
* the clipboard backend.

Everything else - the ``AsyncEventBus``, ``CommandHandler``, the routing
logic in the mouse/keyboard/clipboard base classes, and the server-side
monitor reconciliation - is production code.
"""

from tests.unit import _MOCK_PYNPUT

import asyncio
from contextlib import ExitStack, contextmanager
from typing import Optional
from unittest.mock import MagicMock, patch

_MOCK_PYNPUT()

from event import (  # noqa: E402
    BusEvent,
    BusEventType,
    ActiveScreenChangedEvent,
)
from event.bus import AsyncEventBus  # noqa: E402
from network.protocol.message import ProtocolMessage  # noqa: E402
from network.stream import StreamType  # noqa: E402
from network.data.exchange import MessageExchange, MessageExchangeConfig  # noqa: E402
from model.client import ClientObj, ClientsManager  # noqa: E402
from utils.screen import MonitorLayout  # noqa: E402

# Imported under the pynput mock so the input backends resolve cleanly.
from input.mouse._base import (  # noqa: E402
    ServerMouseListener,
    ServerMouseController,
    ClientMouseController,
)
from input.keyboard._base import (  # noqa: E402
    ServerKeyboardListener,
    ClientKeyboardController,
)
from input.clipboard._base import (  # noqa: E402
    ClipboardListener,
    ClipboardController,
    ClipboardType,
)


SERVER_UID = "server"
DEFAULT_CLIENT_UID = "client1"


# ============================================================================
# Geometry patching
# ============================================================================


def _bboxes_size(bboxes):
    min_x = min(b[0] for b in bboxes)
    min_y = min(b[1] for b in bboxes)
    max_x = max(b[2] for b in bboxes)
    max_y = max(b[3] for b in bboxes)
    return (max_x - min_x, max_y - min_y)


def _bboxes_virtual(bboxes):
    min_x = min(b[0] for b in bboxes)
    min_y = min(b[1] for b in bboxes)
    max_x = max(b[2] for b in bboxes)
    max_y = max(b[3] for b in bboxes)
    return (min_x, min_y, max_x, max_y)


def _geometry_patches(bboxes, primary_index=None):
    """Patch every ``Screen`` accessor the input layer + reconciler read.

    ``input.mouse._base.Screen`` / ``input.keyboard._base.Screen`` /
    ``utils.screen.Screen`` are the same class object, so patching the
    canonical ``utils.screen.Screen`` reflects everywhere.
    """
    layout = MonitorLayout.from_bboxes(bboxes, primary_index=primary_index)
    monitors = list(layout.monitors)
    size = _bboxes_size(bboxes)
    vbbox = _bboxes_virtual(bboxes)
    return [
        patch("utils.screen.Screen.get_size", return_value=size),
        patch("utils.screen.Screen.get_virtual_bbox", return_value=vbbox),
        patch("utils.screen.Screen.get_monitor_layout", return_value=layout),
        patch("utils.screen.Screen.get_monitors", return_value=monitors),
        patch("utils.screen.Screen.get_monitors_cached", return_value=monitors),
    ]


class _Geometry:
    """Context manager applying a whole monitor-geometry patch set."""

    def __init__(self, bboxes, primary_index=None):
        self._patches = _geometry_patches(bboxes, primary_index)
        self._stack = ExitStack()

    def __enter__(self):
        for p in self._patches:
            self._stack.enter_context(p)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stack.close()


# ============================================================================
# Message bridge
# ============================================================================


class BridgeStreamHandler:
    """One side of one logical stream.

    ``send`` builds an outbound frame with the *real* ``MessageExchange``
    builder (identical to :meth:`_ServerStreamHandler._send_logic`) and
    ships the encoded bytes to the peer's :meth:`deliver_bytes`, which
    decodes with ``ProtocolMessage.from_bytes`` and dispatches through
    the peer exchange's registered handler - exercising the production
    encode/decode path while skipping only the socket + TLS + reassembly.
    """

    def __init__(
        self,
        stream_type: int,
        default_source: str,
        default_target: str,
        name: str = "",
    ):
        self.stream_type = stream_type
        self._default_source = default_source
        self._default_target = default_target
        self._exchange = MessageExchange(
            conf=MessageExchangeConfig(auto_dispatch=True),
            id=name or f"bridge-{stream_type}",
        )
        self._peer: Optional["BridgeStreamHandler"] = None
        self._transport_ready = False

    async def connect_to(self, peer: "BridgeStreamHandler") -> None:
        """Route this side's outbound sends to ``peer``'s inbound dispatch."""
        self._peer = peer
        await self._exchange.set_transport(
            send_callback=peer.deliver_bytes,
            receive_callback=None,
            tr_id="default",
        )
        self._transport_ready = True

    def register_receive_callback(self, receive_callback, message_type: str):
        """Mirror ``StreamHandler.register_receive_callback`` semantics."""
        self._exchange.register_handler(message_type, receive_callback)

    async def send(self, data):
        if self._peer is None:
            raise RuntimeError("Bridge send-transport not configured")
        target = getattr(data, "target", None) or self._default_target
        source = getattr(data, "source", None) or self._default_source
        if not isinstance(data, dict) and hasattr(data, "to_dict"):
            data = data.to_dict()
        await self._exchange.send_stream_type_message(
            stream_type=self.stream_type,
            source=source,
            target=target,
            **data,
        )

    def send_nowait(self, data) -> bool:
        # Not on any tested hot path (that stays on the real MOUSE stream),
        # provided only for interface parity. Schedule the async send.
        asyncio.ensure_future(self.send(data))
        return True

    async def deliver_bytes(self, data: bytes) -> None:
        msg = ProtocolMessage.from_bytes(data)
        await self._exchange.dispatch_message(msg)


# ============================================================================
# Cursor guard shim
# ============================================================================


class _CursorGuardShim:
    """Minimal stand-in for ``CursorHandlerWorker._on_screen_change_guard``.

    The real cursor worker (multiprocessing, unavailable here) is what
    turns a ``SCREEN_CHANGE_GUARD`` into an ``ACTIVE_SCREEN_CHANGED`` on
    the server bus. This shim reproduces exactly that translation so the
    server mouse listener/controller react as they would in production.
    """

    def __init__(self, bus: AsyncEventBus):
        self.bus = bus
        bus.subscribe(
            event_type=BusEventType.SCREEN_CHANGE_GUARD,
            callback=self._on_guard,
        )

    async def _on_guard(self, data: Optional[ActiveScreenChangedEvent]):
        if data is None:
            return
        await self.bus.dispatch(
            event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
            data=data,
        )


# ============================================================================
# Fake clipboard
# ============================================================================


class FakeClipboard:
    """Stand-in for the OS clipboard backend.

    Usable both as the ``clipboard`` *class* handed to a
    ``ClipboardListener`` (instantiated with ``on_change`` /
    ``content_types``) and as a ready ``clipboard`` *instance* handed to a
    ``ClipboardController`` (``set_clipboard`` records writes).
    """

    def __init__(self, on_change=None, content_types=None, **_kw):
        self.on_change = on_change
        self.content_types = content_types
        self._listening = False
        self.written: list[str] = []

    def is_listening(self) -> bool:
        return self._listening

    async def start(self):
        self._listening = True

    async def stop(self):
        self._listening = False

    async def set_clipboard(self, content: str) -> bool:
        self.written.append(content)
        return True

    def get_last_content(self) -> Optional[str]:
        return self.written[-1] if self.written else None


def _mouse_controller_mock():
    m = MagicMock(name="MouseController")
    m.position = (0, 0)
    return m


def _keyboard_controller_mock():
    return MagicMock(name="KeyboardController")


# ============================================================================
# Side containers
# ============================================================================


class _ServerSide:
    def __init__(self):
        self.bus: AsyncEventBus = None  # type: ignore[assignment]
        self.listener: ServerMouseListener = None  # type: ignore[assignment]
        self.mouse_controller: ServerMouseController = None  # type: ignore[assignment]
        self.kbd_listener: ServerKeyboardListener = None  # type: ignore[assignment]
        self.clip_listener: ClipboardListener = None  # type: ignore[assignment]
        self.clip_controller: ClipboardController = None  # type: ignore[assignment]
        self.command_handler = None
        self.mouse_mock: MagicMock = None  # type: ignore[assignment]
        self.kbd_mock: MagicMock = None  # type: ignore[assignment]
        self.clipboard: FakeClipboard = None  # type: ignore[assignment]


class _ClientSide:
    def __init__(self):
        self.bus: AsyncEventBus = None  # type: ignore[assignment]
        self.mouse: ClientMouseController = None  # type: ignore[assignment]
        self.kbd: ClientKeyboardController = None  # type: ignore[assignment]
        self.clip_listener: ClipboardListener = None  # type: ignore[assignment]
        self.clip_controller: ClipboardController = None  # type: ignore[assignment]
        self.command_handler = None
        self.mouse_mock: MagicMock = None  # type: ignore[assignment]
        self.kbd_mock: MagicMock = None  # type: ignore[assignment]
        self.clipboard: FakeClipboard = None  # type: ignore[assignment]


class Harness:
    """The wired server<->client graph. Build via :func:`build_bridge`."""

    ClipboardType = ClipboardType

    def __init__(
        self,
        server_bboxes,
        client_bboxes,
        client_uid,
        server_primary=None,
        client_primary=None,
    ):
        self._server_bboxes = server_bboxes
        self._client_bboxes = client_bboxes
        self._server_primary = server_primary
        self._client_primary = client_primary
        self.client_uid = client_uid

        self.server = _ServerSide()
        self.client = _ClientSide()

        self.server_bus = AsyncEventBus()
        self.client_bus = AsyncEventBus()
        self.server.bus = self.server_bus
        self.client.bus = self.client_bus

        self._guard: Optional[_CursorGuardShim] = None
        self._reconciler = None
        self._reconcile_clients: Optional[ClientsManager] = None

        # Bridges (server side / client side).
        self._s_mouse = BridgeStreamHandler(
            StreamType.MOUSE, SERVER_UID, client_uid, "srv-mouse"
        )
        self._c_mouse = BridgeStreamHandler(
            StreamType.MOUSE, client_uid, SERVER_UID, "cli-mouse"
        )
        self._s_kbd = BridgeStreamHandler(
            StreamType.KEYBOARD, SERVER_UID, client_uid, "srv-kbd"
        )
        self._c_kbd = BridgeStreamHandler(
            StreamType.KEYBOARD, client_uid, SERVER_UID, "cli-kbd"
        )
        self._s_clip = BridgeStreamHandler(
            StreamType.CLIPBOARD, SERVER_UID, client_uid, "srv-clip"
        )
        self._c_clip = BridgeStreamHandler(
            StreamType.CLIPBOARD, client_uid, SERVER_UID, "cli-clip"
        )
        self._s_cmd = BridgeStreamHandler(
            StreamType.COMMAND, SERVER_UID, client_uid, "srv-cmd"
        )
        self._c_cmd = BridgeStreamHandler(
            StreamType.COMMAND, client_uid, SERVER_UID, "cli-cmd"
        )

    # -- construction -----------------------------------------------------

    async def _wire_transports(self):
        await self._s_mouse.connect_to(self._c_mouse)
        await self._c_mouse.connect_to(self._s_mouse)
        await self._s_kbd.connect_to(self._c_kbd)
        await self._c_kbd.connect_to(self._s_kbd)
        await self._s_clip.connect_to(self._c_clip)
        await self._c_clip.connect_to(self._s_clip)
        await self._s_cmd.connect_to(self._c_cmd)
        await self._c_cmd.connect_to(self._s_cmd)

    async def build(self) -> "Harness":
        from command import CommandHandler

        await self._wire_transports()

        # -- server components, under the server geometry ------------------
        self.server.mouse_mock = _mouse_controller_mock()
        self.server.kbd_mock = _keyboard_controller_mock()
        self.server.clipboard = FakeClipboard()
        with _Geometry(self._server_bboxes, self._server_primary):
            with (
                patch(
                    "input.mouse._base.MouseController",
                    return_value=self.server.mouse_mock,
                ),
                patch("input.mouse._base.MouseListener"),
                patch(
                    "input.keyboard._base.KeyboardController",
                    return_value=self.server.kbd_mock,
                ),
                patch("input.keyboard._base.KeyboardListener"),
            ):
                self.server.listener = ServerMouseListener(
                    self.server_bus,
                    self._s_mouse,
                    self._s_cmd,
                    filtering=False,
                )
                self.server.mouse_controller = ServerMouseController(self.server_bus)
                self.server.kbd_listener = ServerKeyboardListener(
                    self.server_bus,
                    self._s_kbd,
                    self._s_cmd,
                    filtering=False,
                )
                self.server.clip_listener = ClipboardListener(
                    self.server_bus,
                    self._s_clip,
                    self._s_cmd,
                    clipboard=FakeClipboard,
                )
                self.server.clip_controller = ClipboardController(
                    self.server_bus,
                    self._s_clip,
                    clipboard=self.server.clipboard,
                )
                self.server.command_handler = CommandHandler(
                    self.server_bus, self._s_cmd
                )

        # -- client components, under the client geometry ------------------
        self.client.mouse_mock = _mouse_controller_mock()
        self.client.kbd_mock = _keyboard_controller_mock()
        self.client.clipboard = FakeClipboard()
        with _Geometry(self._client_bboxes, self._client_primary):
            with (
                patch(
                    "input.mouse._base.MouseController",
                    return_value=self.client.mouse_mock,
                ),
                patch(
                    "input.keyboard._base.KeyboardController",
                    return_value=self.client.kbd_mock,
                ),
            ):
                self.client.mouse = ClientMouseController(
                    self.client_bus,
                    self._c_mouse,
                    self._c_cmd,
                )
                self.client.kbd = ClientKeyboardController(
                    self.client_bus,
                    self._c_kbd,
                    self._c_cmd,
                )
                self.client.clip_listener = ClipboardListener(
                    self.client_bus,
                    self._c_clip,
                    self._c_cmd,
                    clipboard=FakeClipboard,
                )
                self.client.clip_controller = ClipboardController(
                    self.client_bus,
                    self._c_clip,
                    clipboard=self.client.clipboard,
                )
                self.client.command_handler = CommandHandler(
                    self.client_bus, self._c_cmd
                )

        # Server-bus SCREEN_CHANGE_GUARD -> ACTIVE_SCREEN_CHANGED shim.
        self._guard = _CursorGuardShim(self.server_bus)
        return self

    # -- server monitor reconciliation (step 2f) --------------------------

    def enable_server_reconciler(self, *client_objs: ClientObj):
        """Attach a partial ``Server`` that runs the real reconcile path.

        Only the collaborators touched by
        ``_on_client_monitors_updated`` -> ``_apply_client_monitors_update``
        -> ``CLIENT_LAYOUT_UPDATED`` are injected onto a ``Server.__new__``
        instance. One or more clients are registered in a real
        ``ClientsManager`` so multi-client reconciliation can be exercised.
        """
        from service.server import Server
        from utils.logging import get_logger

        cm = ClientsManager()
        for client_obj in client_objs:
            cm.add_client(client_obj)
        self._reconcile_clients = cm

        class _StubConfig:
            def __init__(self, manager):
                self.clients_manager = manager

            def get_client(self, uid=None, **_kw):
                return self.clients_manager.get_client(uid=uid)

            def get_clients(self):
                return self.clients_manager.get_clients()

        server = Server.__new__(Server)
        server.event_bus = self.server_bus
        server.config = _StubConfig(cm)
        server._client_locks = {}
        server._known_monitors_signature = ()
        server._notification_callback = None
        server._logger = get_logger("IntegrationReconcileServer")

        async def _save_noop() -> bool:
            return True

        server.save_config = _save_noop

        self.server_bus.subscribe(
            event_type=BusEventType.CLIENT_MONITORS_UPDATED,
            callback=server._on_client_monitors_updated,
        )
        self._reconciler = server
        return server

    @property
    def reconciler(self):
        return self._reconciler

    # -- geometry context helpers ----------------------------------------

    @contextmanager
    def server_geometry(self, bboxes, primary_index=None):
        with _Geometry(bboxes, primary_index):
            yield

    @contextmanager
    def client_geometry(self, bboxes, primary_index=None):
        with _Geometry(bboxes, primary_index):
            yield

    # -- scheduling helpers -----------------------------------------------

    async def settle(self, cycles: int = 8):
        """Let bridge deliveries + worker tasks drain without real sleeps."""
        for _ in range(cycles):
            await asyncio.sleep(0)

    async def wait_until(self, predicate, tries: int = 400) -> bool:
        """Spin until ``predicate()`` is true.

        Loop-yields first (deterministic loop-bound work); only executor-
        bound work - the client keyboard injection runs the OS ``press``
        via ``run_in_executor`` - needs the tiny real-sleep fallback tail.
        """
        for _ in range(tries):
            if predicate():
                return True
            await asyncio.sleep(0)
        for _ in range(100):
            if predicate():
                return True
            await asyncio.sleep(0.001)
        return bool(predicate())

    def track(self, bus: AsyncEventBus, *event_types: int):
        """Collect ``(event_type, data)`` for the given bus event types."""
        events: list[tuple[int, Optional[BusEvent]]] = []

        for et in event_types:

            def _make(target_type):
                async def _cb(data=None):
                    events.append((target_type, data))

                return _cb

            bus.subscribe(event_type=et, callback=_make(et))
        return events

    # -- teardown ----------------------------------------------------------

    async def stop(self):
        try:
            if self.client.mouse is not None:
                await self.client.mouse.stop()
        except Exception:
            pass
        try:
            if self.client.kbd is not None:
                await self.client.kbd.stop()
        except Exception:
            pass


async def build_bridge(
    *,
    server_bboxes=((0, 0, 1920, 1080),),
    client_bboxes=((0, 0, 1920, 1080),),
    client_uid: str = DEFAULT_CLIENT_UID,
    server_primary=None,
    client_primary=None,
) -> Harness:
    """Build and wire a :class:`Harness`.

    ``*_bboxes`` are OS-pixel monitor rectangles ``(min_x, min_y, max_x,
    max_y)`` used only to seed each side's cached geometry at construction.
    """
    h = Harness(
        server_bboxes=list(server_bboxes),
        client_bboxes=list(client_bboxes),
        client_uid=client_uid,
        server_primary=server_primary,
        client_primary=client_primary,
    )
    return await h.build()
