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

import asyncio
from collections import deque
from typing import Optional
from time import time
from threading import Lock

from event import (
    BusEventType,
    MouseEvent,
    EventMapper,
    ClientTopologyCommandEvent,
    ClientTopologyUpdatedEvent,
    CrossScreenCommandEvent,
    ForceScreenChangeCommandEvent,
    ActiveScreenChangedEvent,
    ClientConnectedEvent,
    ClientDisconnectedEvent,
    ClientActiveEvent,
    ClientLayoutUpdatedEvent,
    ScreenSwitchDirectionalRequestEvent,
    ScreenSwitchCycleRequestEvent,
)
from event.bus import EventBus

from network.stream import StreamType
from network.stream.handler import StreamHandler

from utils.logging import get_logger
from utils.screen import Screen
from input.utils import ScreenEdge, EdgeDetector, ButtonMapping

from .backend import MouseListener, MouseController, Button, BACKEND


class ServerMouseListener(object):
    """Base class for server-side mouse listeners."""

    MOVEMENT_HISTORY_N_THRESHOLD = 6
    MOVEMENT_HISTORY_LEN = 8
    # After a return-to-server the movement history is deliberately kept
    # (its edge-ward samples still describe the last push), so a crossing
    # could re-fire on the same edge on the very next tick. This is how far
    # (px) the server cursor must move inward off the just-returned edge
    # before a crossing through it is allowed again.
    RECROSS_UNLOCK_MARGIN = 12

    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
        filtering: bool = True,
    ):
        self.stream = stream_handler
        self.command_stream = command_stream
        self.event_bus = event_bus

        self._listening = False
        self._active_clients: dict[str, bool] = {}
        # Spatial cross-screen routing tables keyed by client UID. Both
        # are pushed verbatim to the active client on activation so the
        # client can resolve return-to-server and intra-client warps
        # against the same data.
        self._edge_bindings_by_client: dict[str, list[dict]] = {}
        self._intra_bindings_by_client: dict[str, list[dict]] = {}

        # Copy-on-write snapshots consumed by the pynput-thread hot path
        # (on_move -> _resolve_cross_screen_target) without locking. Writers
        # hold ``_bindings_write_lock`` and rebuild these tuples; readers
        # do a single atomic ref read (GIL-protected) and iterate the
        # immutable tuple. Tuples never mutate, so an iteration started
        # before a swap finishes on the pre-swap state.
        self._bindings_write_lock = Lock()
        self._edge_bindings_snapshot: tuple[tuple[str, tuple[dict, ...]], ...] = ()
        self._intra_bindings_snapshot: tuple[tuple[str, tuple[dict, ...]], ...] = ()
        self._active_clients_snapshot: tuple[str, ...] = ()
        # One-shot warning per overlapping pair so an ambiguous layout
        # logs once, not every cursor sample.
        self._warned_overlap_keys: set[tuple[str, ...]] = set()
        # Edge detection uses the full MonitorLayout so the outer edges
        # of EACH monitor count - asymmetric layouts where the primary's
        # edges are interior to the union bbox would otherwise miss
        # crossings.
        (
            self._screen_size,
            self._monitor_layout,
            self._screen_bbox,
        ) = self._load_local_geometry()
        self._cross_screen_lock = asyncio.Lock()
        # Set synchronously on the pynput thread the instant a crossing is
        # scheduled, and reset by ``_handle_cross_screen`` when it finishes
        # (or here if scheduling fails). This is the ONLY guard against a
        # back-to-back ``on_move`` sample scheduling a duplicate handler
        # before the coroutine runs.
        self._handling_cross_screen = False
        # Shared between the pynput listener thread and the asyncio loop.
        # Held only across O(1) ops; NEVER across an ``await``.
        self._server_state_lock = Lock()
        self._button_pressed: set[int] = set()

        self._filter_args = {}
        if filtering:
            try:
                import platform

                current_platform = platform.system()
                if current_platform == "Darwin":
                    self._filter_args["darwin_intercept"] = (
                        self._darwin_mouse_suppress_filter
                    )
                elif current_platform == "Windows":
                    self._filter_args["win32_event_filter"] = (
                        self._win32_mouse_suppress_filter
                    )
            except Exception:
                pass

        self._listener = None

        self._movement_history = deque(maxlen=self.MOVEMENT_HISTORY_LEN)
        self._is_dragging = False
        # Server edge the cursor just returned to; crossings through it are
        # suppressed until the cursor moves inward (see the re-cross guard in
        # ``on_move`` / ``_on_active_screen_changed``). ``None`` = no lock.
        # ``_recross_locked_monitor`` pins the monitor whose edge is locked so
        # the inward check uses the right bbox on multi-monitor servers.
        self._recross_locked_edge: Optional[ScreenEdge] = None
        self._recross_locked_monitor = None

        self._logger = get_logger(self.__class__.__name__)

        self._logger.info(
            "mouse listener backend selected",
            backend=BACKEND.get("mouse_listener", "unknown"),
        )

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        self._hotkey_cycle_index = -1

        self._active_client_uid: Optional[str] = None
        # Fallback "from" anchor for the directional hotkey resolver when
        # ``MouseController().position`` fails or the cursor is on a
        # client (OS position is stale). Seeded to the virtual desktop
        # centre so the first press has a plausible default.
        cx = (self._screen_bbox[0] + self._screen_bbox[2]) // 2
        cy = (self._screen_bbox[1] + self._screen_bbox[3]) // 2
        self._last_server_cursor_pos: tuple[float, float] = (float(cx), float(cy))

        self.event_bus.subscribe(
            event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
            callback=self._on_active_screen_changed,
            priority=True,
        )
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_CONNECTED,
            callback=self._on_client_connected,
            priority=True,
        )
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected,
            priority=True,
        )
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_LAYOUT_UPDATED,
            callback=self._on_client_layout_updated,
            priority=True,
        )
        self.event_bus.subscribe(
            event_type=BusEventType.SCREEN_SWITCH_DIRECTIONAL_REQUEST,
            callback=self._on_hotkey_directional,
            priority=True,
        )
        self.event_bus.subscribe(
            event_type=BusEventType.SCREEN_SWITCH_CYCLE_REQUEST,
            callback=self._on_hotkey_cycle,
            priority=True,
        )
        self.event_bus.subscribe(
            event_type=BusEventType.LOCAL_MONITORS_UPDATED,
            callback=self._on_local_monitors_updated,
            priority=True,
        )

    @staticmethod
    def _load_local_geometry():
        """Read the server's current OS monitor geometry from ``Screen``.

        Returns ``(screen_size, monitor_layout, screen_bbox)``. Shared by
        ``__init__`` and the hotplug refresh handler so both build the
        cached geometry the same way.
        """
        screen_size = Screen.get_size()
        monitor_layout = Screen.get_monitor_layout()
        screen_bbox = (
            monitor_layout.virtual_bbox
            if monitor_layout.monitors
            else Screen.get_virtual_bbox()
        )
        return screen_size, monitor_layout, screen_bbox

    async def _on_local_monitors_updated(self, data):
        """Re-read the server's monitor geometry after a local hotplug.

        The pynput hot path reads ``_monitor_layout`` / ``_screen_bbox``
        lock-free, so we build the fresh values first and publish them as
        single atomic reference swaps (GIL-guaranteed). ``MonitorLayout``
        is replaced wholesale, never mutated in place, so an iteration
        started before the swap keeps operating on the old immutable
        object. The write lock only serialises against other writers.
        """
        screen_size, monitor_layout, screen_bbox = self._load_local_geometry()
        with self._bindings_write_lock:
            self._screen_size = screen_size
            self._monitor_layout = monitor_layout
            self._screen_bbox = screen_bbox
        self._logger.info(
            "server monitor geometry refreshed",
            monitors=len(monitor_layout.monitors),
            bbox=screen_bbox,
        )
        await asyncio.sleep(0)

    def _create_listener(self) -> MouseListener:
        return MouseListener(
            on_move=self.on_move,
            on_scroll=self.on_scroll,
            on_click=self.on_click,
            **self._filter_args,
        )

    def start(self) -> bool:
        # Always re-capture the running loop: a previous start() may have
        # cached one that has since been closed (e.g. between tests).
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._logger.warning(
                "No event loop running. "
                "Mouse listener must be started within an async context."
            )

        if not self.is_alive():
            self._listener = self._create_listener()
            self._listener.start()
        self._logger.debug("Started.")
        return True

    LISTENER_JOIN_TIMEOUT = 2.0

    def stop(self) -> bool:
        if self._listener is not None and self.is_alive():
            self._listener.stop()
            try:
                self._listener.join(timeout=self.LISTENER_JOIN_TIMEOUT)
            except RuntimeError:
                pass
            if self._listener.is_alive():
                self._logger.warning(
                    "Mouse listener thread still alive after "
                    f"{self.LISTENER_JOIN_TIMEOUT}s - proceeding without join"
                )
        self._logger.debug("Stopped.")
        return True

    def is_alive(self):
        return self._listener.is_alive() if self._listener else False

    def _rebuild_snapshots(self) -> None:
        """Rebuild the immutable read-side snapshots from the live dicts.

        Caller MUST hold ``_bindings_write_lock``. Each snapshot is a
        tuple of tuples so the hot reader can iterate without locking;
        the GIL guarantees the attribute swap is atomic.
        """
        self._edge_bindings_snapshot = tuple(
            (uid, tuple(b)) for uid, b in self._edge_bindings_by_client.items()
        )
        self._intra_bindings_snapshot = tuple(
            (uid, tuple(b)) for uid, b in self._intra_bindings_by_client.items()
        )
        self._active_clients_snapshot = tuple(self._active_clients.keys())

    async def _on_client_connected(self, data: Optional[ClientConnectedEvent]):
        if data is None:
            return

        client_uid = data.client_uid
        client_streams = data.streams
        if client_streams is None:
            return

        if client_uid and StreamType.MOUSE in client_streams:
            with self._bindings_write_lock:
                # Drop any stale entry from a previous session before
                # re-inserting so a reconnect can't briefly see the
                # old bindings.
                self._active_clients[client_uid] = True
                self._edge_bindings_by_client[client_uid] = list(
                    getattr(data, "edge_bindings", []) or []
                )
                self._intra_bindings_by_client[client_uid] = list(
                    getattr(data, "intra_client_bindings", []) or []
                )
                self._rebuild_snapshots()

        await asyncio.sleep(0)

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        if data is None:
            return

        client_uid = data.client_uid
        with self._bindings_write_lock:
            if client_uid and client_uid in self._active_clients:
                del self._active_clients[client_uid]
            self._edge_bindings_by_client.pop(client_uid, None)
            self._intra_bindings_by_client.pop(client_uid, None)
            self._rebuild_snapshots()

            if not self._active_clients:
                self._listening = False
                # No active clients -> reset the cycle index so the next
                # press after a re-add starts from a sane position.
                self._hotkey_cycle_index = -1

        await asyncio.sleep(0)

    async def _on_client_layout_updated(self, data: Optional[ClientLayoutUpdatedEvent]):
        """Hot-swap a client's cached EdgeBindings after the layout
        editor saves, so changes take effect on the next crossing."""
        if data is None or not data.client_uid:
            return
        with self._bindings_write_lock:
            if data.client_uid in self._active_clients:
                self._edge_bindings_by_client[data.client_uid] = list(
                    data.edge_bindings or []
                )
                self._intra_bindings_by_client[data.client_uid] = list(
                    getattr(data, "intra_client_bindings", []) or []
                )
                self._rebuild_snapshots()

        # Stranded-active recovery: if the client the cursor is CURRENTLY
        # on just lost every server-abutting edge binding (a placed
        # monitor was removed, or a layout edit emptied its placements),
        # there is no longer a return-to-server path - the cursor would
        # stay pinned on a screen that no longer routes back. Force
        # control back to the server, reusing the same primitive as the
        # disconnect path. Done outside the write lock (dispatch awaits).
        if data.client_uid == self._active_client_uid and not (
            data.edge_bindings or []
        ):
            self._logger.info(
                "active client lost all edge bindings; returning to server",
                client_uid=data.client_uid,
            )
            # Route through SCREEN_CHANGE_GUARD (not a bare ACTIVE_SCREEN_CHANGED)
            # so the per-OS listeners run their return-to-server teardown - on
            # macOS the guard handler is what re-couples the mouse and reveals
            # the cursor (_unpin + _restore_cursor); a direct ACTIVE_SCREEN_CHANGED
            # skips it and strands the user with a hidden, decoupled pointer.
            # Mirror the manual return-to-server hotkey exactly.
            await self.event_bus.dispatch(
                event_type=BusEventType.SCREEN_CHANGE_GUARD,
                data=ActiveScreenChangedEvent(active_screen=None),
            )
            await self.command_stream.send(ForceScreenChangeCommandEvent())
        elif data.client_uid == self._active_client_uid:
            # The active client keeps a valid return path, but a server
            # hotplug may have moved the edges / virtual bbox. Re-push the
            # fresh topology (including the updated ``server_bbox`` read by
            # _on_local_monitors_updated) so the client's cached
            # return-to-server landing coords stay correct without waiting
            # for the next crossing.
            try:
                await self.command_stream.send(
                    ClientTopologyCommandEvent(
                        target=data.client_uid,
                        edge_bindings=list(data.edge_bindings or []),
                        server_bbox=self._screen_bbox,
                        intra_client_bindings=list(
                            getattr(data, "intra_client_bindings", []) or []
                        ),
                    )
                )
            except Exception as e:
                self._logger.warning(
                    "failed to re-push topology to active client",
                    client_uid=data.client_uid,
                    error=str(e),
                )
        await asyncio.sleep(0)

    async def _on_hotkey_directional(
        self, data: Optional[ScreenSwitchDirectionalRequestEvent]
    ):
        """Resolve a directional hotkey to an adjacent screen via the layout topology.

        NOTE: directional resolution is anchored to the SERVER monitors
        (``resolve_neighbour`` only matches server-monitor edge bindings).
        It is reliable only while the server owns the cursor, i.e. for
        server -> client. There is no client -> client routing over the
        cursor: the client side knows only return-to-server bindings and
        intra-client (same-client) warps. To move directly between two
        clients, use the Tab / Shift+Tab cycle hotkey (:meth:`_on_hotkey_cycle`),
        which targets a client UID regardless of topology.
        """
        if data is None:
            return

        # When a client is active the OS cursor position is stale (it
        # holds the server's last position before the crossing); refresh
        # from the controller only while the server still owns the cursor.
        x, y = self._last_server_cursor_pos
        if self._listening:
            try:
                from input.mouse.backend import MouseController

                pos = MouseController().position
                if pos and len(pos) == 2:
                    x, y = float(pos[0]), float(pos[1])
                    self._last_server_cursor_pos = (x, y)
            except Exception as e:
                self._logger.debug(
                    "hotkey resolver controller refresh failed",
                    error=str(e),
                )

        client_uid = self.resolve_neighbour(data.edge, x, y)
        if not client_uid:
            self._logger.debug(
                f"Directional hotkey {data.edge} from ({x:.0f}, {y:.0f}) "
                f"resolved no neighbour - no-op."
            )
            return

        if client_uid == self._active_client_uid:
            return

        try:
            await self.event_bus.dispatch(
                event_type=BusEventType.SCREEN_CHANGE_GUARD,
                data=ActiveScreenChangedEvent(active_screen=client_uid),
            )
            await self.command_stream.send(CrossScreenCommandEvent(target=client_uid))
        except Exception as e:
            self._logger.error("hotkey directional switch failed", error=str(e))

    async def _on_hotkey_cycle(self, data: Optional[ScreenSwitchCycleRequestEvent]):
        if data is None:
            return
        # Use the immutable snapshot so an in-flight connect/disconnect
        # can't trip the % len(uids) bounds.
        uids = list(self._active_clients_snapshot)
        if not uids:
            return

        self._hotkey_cycle_index = (self._hotkey_cycle_index + data.direction) % len(
            uids
        )
        client_uid = uids[self._hotkey_cycle_index]
        try:
            await self.event_bus.dispatch(
                event_type=BusEventType.SCREEN_CHANGE_GUARD,
                data=ActiveScreenChangedEvent(active_screen=client_uid),
            )
            await self.command_stream.send(CrossScreenCommandEvent(target=client_uid))
        except Exception as e:
            self._logger.error("hotkey cycle switch failed", error=str(e))

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        if data is None:
            return

        active_screen = data.active_screen

        if active_screen is not None:
            with self._server_state_lock:
                self._movement_history.clear()
            self._listening = True
            self._active_client_uid = active_screen
            # Crossing forward clears any pending return-lock.
            self._recross_locked_edge = None
            self._recross_locked_monitor = None
        else:
            # Don't clear movement history on return-to-server: the
            # samples accumulated before the original crossing describe
            # the push toward the edge and stay relevant for the next
            # crossing in the same direction. A fresh outward push only
            # generates 1-2 ``on_move`` events before the OS clamps
            # against the screen bound, so requiring fresh samples would
            # starve the edge detector.
            self._listening = False
            self._active_client_uid = None
            # ...but that retained, edge-ward history means the very next
            # ``on_move`` could re-cross on the same edge. Lock re-crossing
            # through the returned-to edge until the cursor moves inward.
            self._arm_recross_lock(data.x, data.y)

        await asyncio.sleep(0)

    def _arm_recross_lock(self, x: float, y: float) -> None:
        """Lock re-crossing through the server edge the cursor returned to.

        ``(x, y)`` is the absolute return-landing point supplied by the
        client. Resolves the nearest server monitor and the edge the point
        sits against (within ``RECROSS_UNLOCK_MARGIN``); a no-op when the
        landing carries no coords (legacy path) or isn't near an edge.
        """
        if x < 0 or y < 0:
            self._recross_locked_edge = None
            self._recross_locked_monitor = None
            return
        monitor = self._monitor_layout.nearest_monitor(x, y)
        if monitor is None:
            self._recross_locked_edge = None
            self._recross_locked_monitor = None
            return
        m = self.RECROSS_UNLOCK_MARGIN
        edge = None
        if x <= monitor.min_x + m:
            edge = ScreenEdge.LEFT
        elif x >= monitor.max_x - 1 - m:
            edge = ScreenEdge.RIGHT
        elif y <= monitor.min_y + m:
            edge = ScreenEdge.TOP
        elif y >= monitor.max_y - 1 - m:
            edge = ScreenEdge.BOTTOM
        self._recross_locked_edge = edge
        self._recross_locked_monitor = monitor if edge is not None else None

    def _recross_lock_blocks(self, edge: ScreenEdge, x: float, y: float) -> bool:
        """Whether a crossing through ``edge`` is currently suppressed.

        Clears the lock once the cursor has moved inward past
        ``RECROSS_UNLOCK_MARGIN`` from the locked edge (so a later
        deliberate re-cross through the same edge works), then reports
        whether the pending crossing is the still-locked edge.
        """
        locked = self._recross_locked_edge
        if locked is None:
            return False
        monitor = self._recross_locked_monitor
        m = self.RECROSS_UNLOCK_MARGIN
        moved_inward = False
        if monitor is None:
            moved_inward = True
        elif locked == ScreenEdge.LEFT:
            moved_inward = x > monitor.min_x + m
        elif locked == ScreenEdge.RIGHT:
            moved_inward = x < monitor.max_x - 1 - m
        elif locked == ScreenEdge.TOP:
            moved_inward = y > monitor.min_y + m
        elif locked == ScreenEdge.BOTTOM:
            moved_inward = y < monitor.max_y - 1 - m
        if moved_inward:
            self._recross_locked_edge = None
            self._recross_locked_monitor = None
            return False
        return edge == locked

    def _screen_size_valid(self) -> bool:
        return self._screen_size[0] > 0 and self._screen_size[1] > 0

    def _bbox_span(self) -> "tuple[int, int, int, int, int, int]":
        """Return ``(min_x, min_y, max_x, max_y, width, height)`` with width/height clamped to >= 1."""
        min_x, min_y, max_x, max_y = self._screen_bbox
        return min_x, min_y, max_x, max_y, max(1, max_x - min_x), max(1, max_y - min_y)

    _EDGE_TO_STRING: dict = {
        ScreenEdge.LEFT: "left",
        ScreenEdge.RIGHT: "right",
        ScreenEdge.TOP: "top",
        ScreenEdge.BOTTOM: "bottom",
    }

    def _resolve_cross_screen_target(
        self,
        edge: ScreenEdge,
        cursor_x: float,
        cursor_y: float,
    ) -> Optional[tuple[str, dict, float]]:
        """Match an edge crossing against a cached EdgeBinding.

        Reads the COW snapshot, no lock - the tuple is immutable and the
        attribute swap done by writers is atomic under the GIL.
        """
        # Single atomic ref reads.
        edge_snapshot = self._edge_bindings_snapshot
        active_snapshot = self._active_clients_snapshot
        if not edge_snapshot:
            return None
        edge_str = self._EDGE_TO_STRING.get(edge)
        if not edge_str:
            return None

        monitor = self._monitor_layout.nearest_monitor(cursor_x, cursor_y)
        if monitor is None:
            return None

        m_w = max(1, monitor.max_x - monitor.min_x)
        m_h = max(1, monitor.max_y - monitor.min_y)
        if edge == ScreenEdge.LEFT or edge == ScreenEdge.RIGHT:
            axis_norm = (cursor_y - monitor.min_y) / m_h
        else:
            axis_norm = (cursor_x - monitor.min_x) / m_w
        axis_norm = max(0.0, min(1.0, axis_norm))

        first_match: Optional[tuple[str, dict, float]] = None
        matches: list[str] = []
        for client_uid, bindings in edge_snapshot:
            # Skip clients that disconnected between snapshot rebuilds:
            # the cursor would otherwise warp to a dead peer.
            if client_uid not in active_snapshot:
                continue
            for b in bindings:
                if b.get("server_monitor_id") != monitor.monitor_id:
                    continue
                if b.get("server_edge") != edge_str:
                    continue
                s_start = b.get("server_axis_start", 0.0)
                s_end = b.get("server_axis_end", 0.0)
                if s_start <= axis_norm < s_end:
                    matches.append(client_uid)
                    if first_match is None:
                        first_match = (client_uid, b, axis_norm)
                    break  # one match per client for this edge

        if len(matches) > 1:
            key = tuple(sorted(matches))
            if key not in self._warned_overlap_keys:
                self._warned_overlap_keys.add(key)
                self._logger.warning(
                    "Overlapping cross-screen bindings on the same edge; "
                    "routing to the first match. Disambiguate the layout to fix.",
                    edge=edge_str,
                    candidates=list(matches),
                )
        return first_match

    def resolve_neighbour(
        self,
        edge: ScreenEdge,
        cursor_x: float,
        cursor_y: float,
    ) -> Optional[str]:
        """Which client UID lives off ``edge`` of the server monitor under the cursor."""
        resolved = self._resolve_cross_screen_target(edge, cursor_x, cursor_y)
        return resolved[0] if resolved is not None else None

    def get_active_client_uids(self) -> list[str]:
        """Active client UIDs in insertion order - used by the cycling hotkey."""
        return list(self._active_clients_snapshot)

    def _darwin_mouse_suppress_filter(self, event_type, event):
        raise NotImplementedError("Mouse suppress filter not implemented yet.")

    def _win32_mouse_suppress_filter(self, msg, data):
        raise NotImplementedError("Mouse suppress filter not implemented yet.")

    def on_move(self, x, y):
        if not self._screen_size_valid():
            return True
        # Snapshot the cross-screen guard atomically: ``_handling_cross_screen``
        # is mutated on the event loop, and concurrent moves must observe
        # a consistent value or two handlers could fire.
        with self._server_state_lock:
            if self._handling_cross_screen:
                return True
            should_buffer = not self._listening
            if should_buffer:
                try:
                    self._movement_history.append((x, y))
                except Exception:
                    pass
                # Only update the cached anchor while NOT listening: when
                # the cursor is on a client the OS position is the
                # server's last-known-before-crossing position, not the
                # client's live cursor.
                self._last_server_cursor_pos = (float(x), float(y))
            history_ready = (
                should_buffer
                and len(self._movement_history) >= self.MOVEMENT_HISTORY_N_THRESHOLD
            )

        if not self._listening:
            if history_ready:
                edge = EdgeDetector.is_at_edge(
                    movement_history=self._movement_history,
                    x=x,
                    y=y,
                    screen_size=self._monitor_layout,
                    is_dragging=self._is_dragging,
                )
                if edge is None:
                    return True

                # Suppress an immediate re-cross through the edge the cursor
                # just returned to (the retained history is still edge-ward);
                # the lock clears once the cursor has moved inward off it.
                if self._recross_lock_blocks(edge, x, y):
                    return True

                mouse_event = MouseEvent(x=x, y=y, action=MouseEvent.POSITION_ACTION)

                resolved = self._resolve_cross_screen_target(
                    edge=edge,
                    cursor_x=x,
                    cursor_y=y,
                )
                if resolved is None:
                    return True
                target_screen, binding, server_axis_norm = resolved
                target_monitor_id = binding.get("client_monitor_id")
                if target_monitor_id is not None:
                    target_monitor_id = int(target_monitor_id)

                # Linear map from server-edge axis_norm to client-edge
                # axis_norm. Inlined here to keep the pynput thread off
                # the EdgeBinding import path.
                c_start = binding.get("client_axis_start", 0.0)
                c_end = binding.get("client_axis_end", 0.0)
                s_start = binding.get("server_axis_start", 0.0)
                s_end = binding.get("server_axis_end", 0.0)
                span = s_end - s_start
                if span > 0:
                    local = (server_axis_norm - s_start) / span
                    if local < 0.0:
                        local = 0.0
                    elif local > 1.0:
                        local = 1.0
                    client_axis_norm = c_start + local * (c_end - c_start)
                else:
                    client_axis_norm = c_start

                # ``(x, y)`` is normalised over the destination
                # client monitor's bbox, not the full client virtual
                # desktop - the client denormalises against
                # ``_active_target_bbox``.
                if edge == ScreenEdge.LEFT:
                    mouse_event.x = 1
                    mouse_event.y = client_axis_norm
                elif edge == ScreenEdge.RIGHT:
                    mouse_event.x = 0
                    mouse_event.y = client_axis_norm
                elif edge == ScreenEdge.TOP:
                    mouse_event.x = client_axis_norm
                    mouse_event.y = 1
                elif edge == ScreenEdge.BOTTOM:
                    mouse_event.x = client_axis_norm
                    mouse_event.y = 0

                # Mark the crossing in-flight synchronously BEFORE
                # scheduling: pynput fires ``on_move`` back to back, and
                # the coroutine only flips this flag once it actually
                # runs on the loop. Setting it here closes the window in
                # which a second sample could schedule a duplicate
                # handler. ``_handle_cross_screen`` resets it when done;
                # we reset it here only if scheduling fails, otherwise a
                # dead loop would leave the listener wedged.
                with self._server_state_lock:
                    self._handling_cross_screen = True
                # ``client_edge`` is the client-space edge the cursor enters
                # through; forwarded to the client so it can lock return-to-
                # server against that edge until the cursor moves inward.
                client_entry_edge = binding.get("client_edge")
                if not self._schedule_async(
                    self._handle_cross_screen(
                        edge,
                        mouse_event,
                        target_screen,
                        target_monitor_id,
                        client_entry_edge,
                    )
                ):
                    with self._server_state_lock:
                        self._handling_cross_screen = False

        return True

    def _schedule_async(self, coro) -> bool:
        """Schedule an async coroutine from the pynput thread.

        Returns ``True`` when the coroutine was handed to a running loop,
        ``False`` otherwise - the cross-screen guard relies on this to
        avoid wedging itself when no loop is available.
        """
        if self._loop is not None and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(coro, self._loop)
                return True
            except Exception as e:
                self._logger.error("failed to schedule coroutine", error=str(e))

        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(coro, loop)
            return True
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(coro, loop)
                    return True
                else:
                    self._logger.warning(
                        "Event loop not running. Cannot schedule async operation"
                    )
            except Exception as e:
                self._logger.warning(
                    f"No event loop available for async operation ({e})"
                )
        return False

    async def _handle_cross_screen(
        self,
        edge: ScreenEdge,
        mouse_event: MouseEvent,
        screen: str,
        client_monitor_id: Optional[int] = None,
        client_entry_edge: Optional[str] = None,
    ):
        with self._server_state_lock:
            self._handling_cross_screen = True
        try:
            async with self._cross_screen_lock:
                with self._server_state_lock:
                    history_len = len(self._movement_history)
                if history_len < self.MOVEMENT_HISTORY_N_THRESHOLD:
                    return

                with self._server_state_lock:
                    self._movement_history.clear()

                await self.event_bus.dispatch(
                    event_type=BusEventType.SCREEN_CHANGE_GUARD,
                    data=ActiveScreenChangedEvent(active_screen=screen),
                )

                # Push the topology to the activating client so it can
                # resolve return-to-server crossings AND enforce the
                # workspace topology over its OS-level monitor adjacency.
                bindings = self._edge_bindings_by_client.get(screen) or []
                intra_bindings = self._intra_bindings_by_client.get(screen) or []
                if bindings or intra_bindings:
                    await self.command_stream.send(
                        ClientTopologyCommandEvent(
                            target=screen,
                            edge_bindings=bindings,
                            server_bbox=self._screen_bbox,
                            intra_client_bindings=intra_bindings,
                        )
                    )

                # Carry the landing coords on the activation packet
                # itself: the mouse stream can outrun the command stream
                # and a POSITION_ACTION delivered before ``_is_active``
                # flips True is silently dropped, leaving the cursor at
                # screen centre. The parallel POSITION_ACTION below is
                # kept for old clients that don't read ``position_x/_y``
                # off CLIENT_ACTIVE - idempotent on new clients.
                await self.command_stream.send(
                    CrossScreenCommandEvent(
                        target=screen,
                        client_monitor_id=client_monitor_id,
                        x=mouse_event.x,
                        y=mouse_event.y,
                        entry_edge=client_entry_edge,
                    )
                )
                await self.stream.send(mouse_event)
                await asyncio.sleep(0)

        except Exception as e:
            self._logger.error("failed to handle cross-screen", error=str(e))
        finally:
            with self._server_state_lock:
                self._handling_cross_screen = False

    def on_click(self, x, y, button: Button, pressed):
        if self._listening:
            if not self._screen_size_valid():
                return True
            button = ButtonMapping[button.name].value
            # Normalise over the virtual-desktop bbox so a click on a
            # secondary monitor doesn't end up past the primary's [0, 1].
            min_x, min_y, _max_x, _max_y, width, height = self._bbox_span()
            mouse_event = MouseEvent(
                x=(x - min_x) / width,
                y=(y - min_y) / height,
                button=button,
                action=MouseEvent.CLICK_ACTION,
                is_pressed=pressed,
            )
            try:
                if not pressed and button in self._button_pressed:
                    self._button_pressed.remove(button)
                elif pressed:
                    self._button_pressed.add(button)
                else:
                    return True

                self._schedule_async(self.stream.send(mouse_event))
            except Exception as e:
                self._logger.error("failed to dispatch mouse click", error=str(e))

        else:
            self._is_dragging = pressed and ButtonMapping[button.name].value in [
                ButtonMapping.left.value,
                ButtonMapping.right.value,
            ]
        return True

    def on_scroll(self, x, y, dx, dy):
        if self._listening:
            mouse_event = MouseEvent(dx=dx, dy=dy, action=MouseEvent.SCROLL_ACTION)
            try:
                self._schedule_async(self.stream.send(mouse_event))
            except Exception as e:
                self._logger.error("failed to dispatch mouse scroll", error=str(e))
        return True


class ServerMouseController(object):
    """Base class for server-side mouse controllers."""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus

        self._screen_size: tuple[int, int] = Screen.get_size()
        # Spans every connected monitor; the cursor may need to land on
        # any of them when control returns from a client.
        self._screen_bbox: tuple[int, int, int, int] = Screen.get_virtual_bbox()

        self._controller = MouseController()
        self._logger = get_logger(self.__class__.__name__)

        self._logger.info(
            "mouse controller backend selected",
            backend=BACKEND.get("mouse_controller", "unknown"),
        )

        self.event_bus.subscribe(
            event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
            callback=self._on_active_screen_changed,
        )
        self.event_bus.subscribe(
            event_type=BusEventType.LOCAL_MONITORS_UPDATED,
            callback=self._on_local_monitors_updated,
        )

    async def _on_local_monitors_updated(self, data):
        """Refresh the virtual-desktop bbox after a local monitor hotplug.

        ``_screen_bbox`` is read only on the event loop by
        ``position_cursor`` (return-to-server placement), so a plain
        assignment is safe (single writer, GIL-atomic ref swap).
        """
        self._screen_size = Screen.get_size()
        self._screen_bbox = Screen.get_virtual_bbox()
        self._logger.info(
            "server controller geometry refreshed", bbox=self._screen_bbox
        )
        await asyncio.sleep(0)

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        """Reposition the server cursor when control returns (active screen None)."""
        if data is not None:
            active_screen = data.active_screen
            if active_screen is None:
                x = data.x
                y = data.y
                if x > -1 and y > -1:
                    # Position multiple times so absolute placement
                    # converges across platforms. This is NOT replaceable
                    # by a "set + read-back + retry" loop: on some OSes the
                    # controller reports the target position immediately
                    # while the real cursor has not moved yet, so a
                    # read-back check would pass spuriously and stop early.
                    # The fixed-repeat write is the reliable workaround -
                    # do not "optimize" it into a verified single set.
                    for _ in range(50):
                        self.position_cursor(x, y)

        await asyncio.sleep(0)

    def position_cursor(self, x: float | int, y: float | int):
        """Place the cursor from normalised ``(x, y)`` over the virtual desktop bbox."""
        try:
            min_x, min_y, max_x, max_y = self._screen_bbox
            width = max_x - min_x
            height = max_y - min_y
            if width <= 0 or height <= 0:
                return
            x = max(min_x, min(max_x - 1, round(min_x + x * width)))
            y = max(min_y, min(max_y - 1, round(min_y + y * height)))
        except ValueError:
            self._logger.error("invalid cursor coordinates", x=x, y=y)
            return

        try:
            self._controller.position = (x, y)
        except Exception as e:
            self._logger.error("failed to position cursor", error=str(e))


class ClientMouseController(object):
    """Async client-side mouse controller (movements, clicks, scrolls)."""

    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5
    # Consecutive presses on the same button within this window are
    # tagged as a multi-click sequence (double, triple, ...).
    DOUBLE_CLICK_THRESHOLD = 0.4
    MAX_CLICK_COUNT = 10
    # Gap (seconds) in the incoming move stream beyond which the position
    # history is dropped. The history only grows while moves arrive, so a
    # pause (typing with the mouse still, a game grab) would otherwise leave
    # it frozen with pre-pause samples and let ``_detect_directed_edge`` vote
    # on a direction that no longer applies on the very next move.
    # ``_detect_edge_via_delta`` still covers the current tick.
    MOVE_STREAM_GAP_SECONDS = 0.15
    # Consecutive forwarded moves that the OS did not apply to the cursor
    # before edge routing is suspended. A foreground app holding the pointer
    # (a game's cursor grab) produces an unbounded run of these: the cursor is
    # not where routing thinks it is, and ``_detect_edge_via_delta`` would
    # happily read "pushing at the edge" from deltas that move nothing, then
    # clamp - warping the cursor the game is trying to keep still. Routing
    # resumes the moment the cursor moves again. Backends that always apply
    # the delta (Windows, Linux, the macOS CGEvent fallback) never reach this.
    IMMOBILE_MOVES_BEFORE_HOLD = 3
    # Hysteresis for the entry-edge return lock. After a crossing the cursor
    # lands ON the entry edge, which is also the edge used to return to the
    # server; a single reverse HID jitter would otherwise bounce control
    # straight back. Return-to-server through the entry edge is gated by the
    # LAG-FREE ``_inward_travel`` (net perpendicular offset from that edge,
    # accumulated from the injected deltas - not the async, laggy cursor
    # read-back that misleads edge detection on fast motion):
    #   - it arms only once the cursor has genuinely moved inward past
    #     ``RETURN_ARM_MARGIN`` (kills the at-landing jitter);
    #   - once armed, the return fires only when the cursor has come back to
    #     within ``RETURN_RELEASE_MARGIN`` of the edge (so a cursor sitting far
    #     inside can't be bounced back by a stray reverse delta while the OS
    #     read-back still reports the edge).
    RETURN_ARM_MARGIN = 12
    RETURN_RELEASE_MARGIN = 4

    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
    ):
        self.stream = stream_handler
        self.command_stream = command_stream
        self.event_bus = event_bus
        self._cross_screen_event = asyncio.Event()
        self._edge_check_lock = asyncio.Lock()
        self._checking_edge = False

        self._is_active = False
        self._current_screen = None
        # Target monitor signalled by the server's most recent
        # CrossScreenCommandEvent. ``None`` falls back to the virtual
        # desktop bbox (legacy / single-monitor client).
        self._active_monitor_id: Optional[int] = None
        # Topology pushed by the server: each entry carries both
        # server-side and client-side axis ranges, so the same dict
        # drives the server's forward routing AND the client's
        # return-to-server lookup here (via ``client_*`` fields).
        self._edge_bindings: list[dict] = []
        # Cross-monitor warp bindings within this client. Used to
        # enforce the workspace topology over the OS-level adjacency:
        # an unbound OS-driven drift is reverted; a bound transition
        # is honoured via explicit warp.
        self._intra_client_bindings: list[dict] = []
        # O(1) lookups derived from ``_intra_client_bindings``.
        # Rebuilt only when the server pushes a topology.
        self._intra_by_src: dict[int, list[dict]] = {}
        self._intra_pairs: set[tuple[int, int]] = set()
        # Monitor last observed under the cursor - used to detect
        # OS-driven drift between client monitors.
        self._last_known_monitor_id: Optional[int] = None
        # Fast-path cache for ``find_monitor_at`` (cursor usually stays
        # within the same monitor across ticks).
        self._cached_monitor = None
        # Latest relative MOVE_ACTION delta from the server. Used as a
        # direction fallback when the OS clamps the cursor against a
        # monitor bound and the position history stalls - without it
        # the cursor pinned at ``x = monitor.min_x`` can't trigger the
        # return-to-server crossing.
        self._last_move_delta: tuple[int, int] = (0, 0)
        # Return-to-server lockout (hysteretic). After a crossing the cursor
        # lands ON the entry edge, which is also the edge used to return to the
        # server. ``_return_locked_edge`` names that edge; ``_inward_travel`` is
        # the lag-free net perpendicular offset from it (accumulated from the
        # injected HID deltas, never the async cursor read-back), and
        # ``_return_armed`` latches once the cursor has genuinely entered past
        # ``RETURN_ARM_MARGIN``. The entry-edge return then fires only when the
        # offset falls back to ``RETURN_RELEASE_MARGIN`` - see
        # ``_accumulate_inward_travel`` and the gate in ``_check_edge``.
        self._return_locked_edge: Optional[ScreenEdge] = None
        self._inward_travel: int = 0
        self._return_armed: bool = False
        # Server's virtual desktop bbox - return-to-server (x, y) is
        # normalised over this.
        self._server_bbox: Optional[tuple[int, int, int, int]] = None
        self._screen_size: tuple[int, int] = Screen.get_size()
        self._monitor_layout = Screen.get_monitor_layout()
        self._screen_bbox: tuple[int, int, int, int] = (
            self._monitor_layout.virtual_bbox
            if self._monitor_layout.monitors
            else Screen.get_virtual_bbox()
        )
        # Pre-resolved on activation so every mouse tick avoids the
        # O(N) monitor scan.
        self._active_target_bbox: tuple[int, int, int, int] = self._screen_bbox

        self._movement_history = deque(maxlen=self.MOVEMENT_HISTORY_LEN)

        self._controller = MouseController()
        self._pressed = False
        self._previous_button: int | None = None
        self._last_press_time: float = -99
        self._click_count: int = 0
        self._is_dragging = False

        # When the last forwarded move was processed, so a pause in the
        # stream can invalidate the position history
        # (``MOVE_STREAM_GAP_SECONDS``).
        self._last_move_ts: float = 0.0
        # Cursor position observed at the previous relative injection, for
        # backends that MEASURE the displacement the OS actually applied
        # instead of assuming it equals the delta (see the macOS
        # ``_inject_relative``). Cleared on every absolute placement, whose
        # jump is not travel.
        self._last_seen_pos: Optional[tuple[float, float]] = None
        # Run length of forwarded moves the OS did not apply, maintained by
        # the backends that can tell (see ``IMMOBILE_MOVES_BEFORE_HOLD``).
        self._immobile_moves: int = 0

        self._logger = get_logger(self.__class__.__name__)

        self._logger.info(
            "mouse controller backend selected",
            backend=BACKEND.get("mouse_controller", "unknown"),
        )

        if not self.check_cursor_validity():
            raise RuntimeError("No valid cursor found.")

        self._queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

        self.stream.register_receive_callback(
            self._mouse_event_callback, message_type="mouse"
        )

        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_ACTIVE, callback=self._on_client_active
        )
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_INACTIVE, callback=self._on_client_inactive
        )
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_TOPOLOGY_UPDATED,
            callback=self._on_client_topology_updated,
        )
        self.event_bus.subscribe(
            event_type=BusEventType.LOCAL_MONITORS_UPDATED,
            callback=self._on_local_monitors_updated,
        )

    async def _force_return_to_server(self, x: float = -1, y: float = -1) -> None:
        """Hand control back to the server unconditionally.

        Reused by the return-to-server paths (edge binding resolved, OS
        drift) and by the stranded-active recovery when no landing point
        can be computed. ``x=y=-1`` means "return control, leave the
        server cursor where it is" - ``ServerMouseController`` only
        repositions when both coords are > -1.
        """
        self._cross_screen_event.set()
        self._movement_history.clear()
        try:
            await self.command_stream.send(CrossScreenCommandEvent(x=x, y=y))
        except Exception as e:
            self._logger.error("failed to send return-to-server", error=str(e))
        await self.event_bus.dispatch(
            event_type=BusEventType.CLIENT_INACTIVE, data=None
        )

    async def _on_local_monitors_updated(self, data):
        """Re-read this client's monitor geometry after a local hotplug.

        Runs on the same event loop as the edge-check worker, so the
        attribute swaps below are safe as long as no ``await`` is
        interleaved between them. If the monitor the cursor is currently
        active on just vanished, its return-to-server binding is gone too,
        so force control back to the server (after the swaps).
        """
        screen_size = Screen.get_size()
        monitor_layout = Screen.get_monitor_layout()
        screen_bbox = (
            monitor_layout.virtual_bbox
            if monitor_layout.monitors
            else Screen.get_virtual_bbox()
        )
        known_ids = {m.monitor_id for m in monitor_layout.monitors}
        stranded = (
            self._is_active
            and self._active_monitor_id is not None
            and self._active_monitor_id not in known_ids
        )

        # Atomic swaps, no await interleaved.
        self._screen_size = screen_size
        self._monitor_layout = monitor_layout
        self._screen_bbox = screen_bbox
        self._cached_monitor = None
        if self._active_monitor_id is not None and self._active_monitor_id in known_ids:
            for m in monitor_layout.monitors:
                if m.monitor_id == self._active_monitor_id:
                    self._active_target_bbox = (m.min_x, m.min_y, m.max_x, m.max_y)
                    break
        else:
            self._active_target_bbox = screen_bbox

        self._logger.info(
            "client monitor geometry refreshed",
            monitors=len(monitor_layout.monitors),
            bbox=screen_bbox,
            stranded=stranded,
        )

        if stranded:
            self._logger.info(
                "active monitor removed; returning control to server",
                active_monitor_id=self._active_monitor_id,
            )
            await self._force_return_to_server()
        else:
            await asyncio.sleep(0)

    def check_cursor_validity(self) -> bool:
        """Ensure a cursor is available - may fail on Windows when no cursor is present."""
        try:
            return self._cursor_position() is not None
        except Exception:
            self._logger.error("cursor not available")
            return False

    async def start(self):
        if not self._running:
            self._running = True
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                finally:
                    await asyncio.sleep(0)

            self._worker_task = asyncio.create_task(self._run_worker())
            self._logger.debug("async worker started")
            await asyncio.sleep(0)

    async def stop(self):
        if self._running:
            self._running = False

            if self._worker_task:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
                self._worker_task = None

            self._logger.debug("async worker stopped")

    def is_alive(self) -> bool:
        return (
            self._running
            and self._worker_task is not None
            and not self._worker_task.done()
        )

    async def _run_worker(self):
        while self._running:
            try:
                message = await self._queue.get()

                event = EventMapper.get_event(message)
                if not isinstance(event, MouseEvent):
                    continue

                if event.action == MouseEvent.MOVE_ACTION:
                    # A pause in the stream (typing with the mouse still, a
                    # game holding the pointer) leaves the position history
                    # frozen with pre-pause samples - see
                    # ``MOVE_STREAM_GAP_SECONDS``.
                    now = time()
                    if (
                        self._last_move_ts > 0.0
                        and now - self._last_move_ts > self.MOVE_STREAM_GAP_SECONDS
                    ):
                        self._movement_history.clear()
                    self._last_move_ts = now

                    self._move_cursor(event.x, event.y, event.dx, event.dy)
                    # Routing is meaningless while the OS is withholding our
                    # motion - see ``IMMOBILE_MOVES_BEFORE_HOLD``.
                    if self._immobile_moves < self.IMMOBILE_MOVES_BEFORE_HOLD:
                        await self._check_edge()
                elif event.action == MouseEvent.POSITION_ACTION:
                    # Position multiple times so absolute placement
                    # converges across platforms. A read-back check can't
                    # replace this: some OSes report the target position
                    # before the cursor actually moves (see the matching
                    # note in ServerMouseController._on_active_screen_changed).
                    for _ in range(10):
                        await self._position_cursor(event.x, event.y)
                    await self._check_edge()
                elif event.action == MouseEvent.CLICK_ACTION:
                    self._click(event.button, event.is_pressed)
                elif event.action == MouseEvent.SCROLL_ACTION:
                    self._scroll(event.dx, event.dy)

                await asyncio.sleep(0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error("worker error", error=str(e))
                await asyncio.sleep(0.01)

    async def _on_client_active(self, data: Optional[ClientActiveEvent]):
        # Control just arrived: the one moment where retrying a degraded
        # injection backend costs nothing and can recover the session.
        self._on_relative_injection_degraded()

        if data is not None:
            self._current_screen = data.client_uid
            self._active_monitor_id = data.client_monitor_id

            self._active_target_bbox = self._screen_bbox
            if self._active_monitor_id is not None and self._monitor_layout.monitors:
                for m in self._monitor_layout.monitors:
                    if m.monitor_id == self._active_monitor_id:
                        self._active_target_bbox = (m.min_x, m.min_y, m.max_x, m.max_y)
                        break
            # Prime "last seen" with the landing target so the first
            # ``_check_edge`` doesn't flag the landing as drift.
            self._last_known_monitor_id = self._active_monitor_id
        self._movement_history.clear()
        self._last_move_delta = (0, 0)
        self._last_move_ts = 0.0
        self._last_seen_pos = None
        self._immobile_moves = 0

        # Lock return-to-server against the edge the cursor entered through
        # (server-supplied, else inferred from the landing coords) until it
        # travels inward - see ``_accumulate_inward_travel``.
        self._return_locked_edge = (
            self._resolve_entry_edge(
                getattr(data, "entry_edge", None),
                data.position_x,
                data.position_y,
            )
            if data is not None
            else None
        )
        self._inward_travel = 0
        self._return_armed = False

        self._is_active = True
        self._cross_screen_event.clear()

        if not self._running:
            await self.start()

        # Position at the landing point if the server packed the coords
        # into CLIENT_ACTIVE. Done AFTER ``_is_active`` flips True so
        # the parallel POSITION_ACTION on the mouse stream can't race
        # the activation event and silently drop the landing. The
        # fixed-repeat write is intentional (see the note in
        # ServerMouseController._on_active_screen_changed) - a read-back
        # check would pass before the cursor really moves on some OSes.
        if data is not None and data.position_x >= 0 and data.position_y >= 0:
            for _ in range(10):
                await self._position_cursor(data.position_x, data.position_y)

    async def _on_client_inactive(self, data: Optional[ClientActiveEvent]):
        self._movement_history.clear()
        self._cross_screen_event.clear()
        self._is_active = False
        self._active_monitor_id = None
        self._active_target_bbox = self._screen_bbox
        self._last_known_monitor_id = None
        self._last_move_delta = (0, 0)
        self._return_locked_edge = None
        self._inward_travel = 0
        self._return_armed = False
        self._last_move_ts = 0.0
        self._last_seen_pos = None
        self._immobile_moves = 0

    _STRING_TO_EDGE_CLIENT: dict = {
        "left": ScreenEdge.LEFT,
        "right": ScreenEdge.RIGHT,
        "top": ScreenEdge.TOP,
        "bottom": ScreenEdge.BOTTOM,
    }

    def _resolve_entry_edge(
        self, entry_edge: Optional[str], pos_x: float, pos_y: float
    ) -> Optional[ScreenEdge]:
        """Client-space edge the cursor entered through.

        Prefers the server-supplied ``entry_edge`` (authoritative); falls
        back to inferring it from the normalised landing coords for older
        servers that don't send it. A landing sits on exactly one edge, so
        only the axis pinned to 0/1 identifies it. Returns ``None`` when no
        explicit landing was requested (legacy / hotkey path).
        """
        edge = self._STRING_TO_EDGE_CLIENT.get(entry_edge or "")
        if edge is not None:
            return edge
        if pos_x < 0 or pos_y < 0:
            return None
        eps = 1e-3
        if pos_x <= eps:
            return ScreenEdge.LEFT
        if pos_x >= 1.0 - eps:
            return ScreenEdge.RIGHT
        if pos_y <= eps:
            return ScreenEdge.TOP
        if pos_y >= 1.0 - eps:
            return ScreenEdge.BOTTOM
        return None

    def _accumulate_inward_travel(self, dx: int, dy: int) -> None:
        """Track the cursor's offset from the entry edge from a lag-free delta.

        Maintains ``_inward_travel`` = net signed displacement along the axis
        perpendicular to ``_return_locked_edge`` (direction/angle-agnostic: a
        diagonal move contributes only its perpendicular component, movement
        parallel to the edge contributes nothing). Because the landing sits on
        the edge, this IS the cursor's perpendicular offset from it - clamped to
        ``[0, monitor span]`` so it stays faithful to the OS-clamped cursor
        (never reset to 0 by a margin): an edge-ward jitter reduces the net so
        the offset keeps tracking the true distance from the edge. Once it reaches
        ``RETURN_ARM_MARGIN`` the cursor has genuinely entered, so the return
        lock is *armed*; the actual return is then gated on the offset falling
        back to ``RETURN_RELEASE_MARGIN`` in ``_check_edge`` (hysteresis). This
        never trusts the async, laggy OS read-back, so a fast crossing that
        parks the cursor far inside can't be bounced back to the server.
        """
        edge = self._return_locked_edge
        if edge is None:
            return
        min_x, min_y, max_x, max_y = self._active_target_bbox
        if edge == ScreenEdge.LEFT:
            self._inward_travel += dx
            span = max_x - min_x
        elif edge == ScreenEdge.RIGHT:
            self._inward_travel -= dx
            span = max_x - min_x
        elif edge == ScreenEdge.TOP:
            self._inward_travel += dy
            span = max_y - min_y
        elif edge == ScreenEdge.BOTTOM:
            self._inward_travel -= dy
            span = max_y - min_y
        else:
            return
        # Clamp to the perpendicular extent of the active monitor. The ceiling
        # is load-bearing for the backends that credit the RAW delta (Windows,
        # Linux): the cursor is pinned by the OS at the screen edges, but the
        # server keeps forwarding deltas while the user pushes, so without it
        # the offset diverges far past the monitor and a real return sweep can
        # never bring it back into the release band (control gets stuck on the
        # client). Where the applied displacement is MEASURED instead (macOS)
        # the offset stops growing on its own - the OS swallows the delta at
        # the edge, so ``applied`` is (0, 0) - and the ceiling is belt and
        # braces. The 0 floor is needed everywhere: it self-resyncs, since
        # pushing into the entry edge drives the offset to 0 so the return gate
        # reliably opens there. Skip on a degenerate bbox, else the floor would
        # cap arming at 0.
        if span > 0:
            self._inward_travel = max(0, min(span, self._inward_travel))
        if self._inward_travel >= self.RETURN_ARM_MARGIN:
            self._return_armed = True

    async def _on_client_topology_updated(
        self, data: Optional[ClientTopologyUpdatedEvent]
    ):
        """Cache the topology pushed by the server for ``_check_edge``."""
        if data is None:
            return
        self._edge_bindings = list(data.edge_bindings or [])
        self._intra_client_bindings = list(
            getattr(data, "intra_client_bindings", []) or []
        )
        # Pre-build O(1) lookups for the hot path.
        by_src: dict[int, list[dict]] = {}
        pairs: set[tuple[int, int]] = set()
        for b in self._intra_client_bindings:
            src_id = b.get("src_monitor_id")
            dst_id = b.get("dst_monitor_id")
            if src_id is None or dst_id is None:
                continue
            by_src.setdefault(int(src_id), []).append(b)
            pairs.add((int(src_id), int(dst_id)))
        self._intra_by_src = by_src
        self._intra_pairs = pairs
        if data.server_bbox:
            try:
                self._server_bbox = (
                    int(data.server_bbox[0]),
                    int(data.server_bbox[1]),
                    int(data.server_bbox[2]),
                    int(data.server_bbox[3]),
                )
            except (TypeError, ValueError, IndexError):
                self._server_bbox = None
        await asyncio.sleep(0)

    _EDGE_TO_STRING_CLIENT: dict = {
        ScreenEdge.LEFT: "left",
        ScreenEdge.RIGHT: "right",
        ScreenEdge.TOP: "top",
        ScreenEdge.BOTTOM: "bottom",
    }

    def _find_monitor_for_cursor(self, x: float, y: float):
        """Monitor containing ``(x, y)``, or the nearest one for L-shape dead zones."""
        cached = self._cached_monitor
        if cached is not None and cached.contains(x, y):
            return cached
        m = self._monitor_layout.nearest_monitor(x, y)
        if m is not None:
            self._cached_monitor = m
        return m

    def _detect_edge_via_delta(
        self,
        x: float,
        y: float,
        monitor,
    ) -> Optional[ScreenEdge]:
        """Edge detection via the latest forwarded delta.

        Fallback for when the OS clamps the cursor against a monitor
        bound - the position history stalls but the delta still reveals
        which edge the user is pushing toward.

        Boundary convention: "cursor sits ON the inner edge pixel, still
        inside the monitor" - ``x <= min_x`` (column 0) / ``x >= max_x-1``
        (last valid column, since a monitor occupies ``[min, max)``). This
        is deliberately different from :meth:`_infer_exit_edge`, which
        tests "already OUTSIDE the box". Do not unify the two - they run
        in different contexts and merging the comparisons breaks the drift
        handler.
        """
        if monitor is None:
            return None
        dx, dy = self._last_move_delta
        if dx == 0 and dy == 0:
            return None
        if x <= monitor.min_x and dx < 0:
            return ScreenEdge.LEFT
        if x >= monitor.max_x - 1 and dx > 0:
            return ScreenEdge.RIGHT
        if y <= monitor.min_y and dy < 0:
            return ScreenEdge.TOP
        if y >= monitor.max_y - 1 and dy > 0:
            return ScreenEdge.BOTTOM
        return None

    @staticmethod
    def _detect_directed_edge(
        movement_history,
        x: float,
        y: float,
        monitor,
        direction_ratio: float = 0.85,
    ) -> Optional[ScreenEdge]:
        """Edge approach detection on a single monitor, ignoring OS adjacency.

        :meth:`EdgeDetector.is_at_edge` filters edges with an OS-level
        neighbour, which is right on the server (OS layout = intent)
        but wrong on the client when the workspace topology contradicts
        the OS one.

        Boundary convention: "cursor ON the inner edge pixel, still
        inside" (``x <= min_x`` / ``x >= max_x-1``) - same as
        :meth:`_detect_edge_via_delta` and distinct from
        :meth:`_infer_exit_edge` (which tests "already outside").
        """
        size = len(movement_history)
        if size < 2 or monitor is None:
            return None

        x_edge = None
        x_axis_sign = 0
        if x <= monitor.min_x:
            x_edge = ScreenEdge.LEFT
            x_axis_sign = -1
        elif x >= monitor.max_x - 1:
            x_edge = ScreenEdge.RIGHT
            x_axis_sign = 1

        y_edge = None
        y_axis_sign = 0
        if y <= monitor.min_y:
            y_edge = ScreenEdge.TOP
            y_axis_sign = -1
        elif y >= monitor.max_y - 1:
            y_edge = ScreenEdge.BOTTOM
            y_axis_sign = 1

        if x_edge is None and y_edge is None:
            return None

        pairs = size - 1
        min_agreements = int(pairs * direction_ratio)

        if x_edge is not None:
            agreements = 0
            for i in range(pairs):
                if (
                    movement_history[i + 1][0] - movement_history[i][0]
                ) * x_axis_sign > 0:
                    agreements += 1
            if agreements >= min_agreements:
                return x_edge

        if y_edge is not None:
            agreements = 0
            for i in range(pairs):
                if (
                    movement_history[i + 1][1] - movement_history[i][1]
                ) * y_axis_sign > 0:
                    agreements += 1
            if agreements >= min_agreements:
                return y_edge

        return None

    def _resolve_intra_client_warp(
        self,
        edge: ScreenEdge,
        x: float,
        y: float,
        monitor,
    ) -> Optional[tuple[int, float, float, str]]:
        """Match an edge approach against an intra-client binding.

        Returns ``(dst_monitor_id, target_x, target_y, dst_edge)`` or
        ``None`` when no binding covers ``(monitor, edge, axis_norm)``.
        ``dst_edge`` is the destination edge the cursor lands just inside of.
        """
        if monitor is None:
            return None
        candidates = self._intra_by_src.get(monitor.monitor_id)
        if not candidates:
            return None
        edge_str = self._EDGE_TO_STRING_CLIENT.get(edge)
        if edge_str is None:
            return None

        m_w = max(1, monitor.max_x - monitor.min_x)
        m_h = max(1, monitor.max_y - monitor.min_y)
        if edge == ScreenEdge.LEFT or edge == ScreenEdge.RIGHT:
            axis_norm = (y - monitor.min_y) / m_h
        else:
            axis_norm = (x - monitor.min_x) / m_w
        axis_norm = max(0.0, min(1.0, axis_norm))

        for b in candidates:
            if b.get("src_edge") != edge_str:
                continue
            s_start = b.get("src_axis_start", 0.0)
            s_end = b.get("src_axis_end", 0.0)
            if s_end <= s_start or not (s_start <= axis_norm < s_end):
                continue

            local = (axis_norm - s_start) / (s_end - s_start)
            if local < 0.0:
                local = 0.0
            elif local > 1.0:
                local = 1.0
            d_start = b.get("dst_axis_start", 0.0)
            d_end = b.get("dst_axis_end", 0.0)
            dst_axis = d_start + local * (d_end - d_start)

            dst_id = int(b.get("dst_monitor_id", -1))
            dst_edge = b.get("dst_edge")
            d_min_x = int(b.get("dst_monitor_min_x", 0))
            d_min_y = int(b.get("dst_monitor_min_y", 0))
            d_max_x = int(b.get("dst_monitor_max_x", 0))
            d_max_y = int(b.get("dst_monitor_max_y", 0))
            d_w = max(1, d_max_x - d_min_x)
            d_h = max(1, d_max_y - d_min_y)

            # Land JUST INSIDE the destination edge so the next tick
            # doesn't re-trigger the same warp from the other side.
            if dst_edge == "right":
                target_x = d_max_x - 2
                target_y = d_min_y + dst_axis * d_h
            elif dst_edge == "left":
                target_x = d_min_x + 1
                target_y = d_min_y + dst_axis * d_h
            elif dst_edge == "bottom":
                target_x = d_min_x + dst_axis * d_w
                target_y = d_max_y - 2
            elif dst_edge == "top":
                target_x = d_min_x + dst_axis * d_w
                target_y = d_min_y + 1
            else:
                continue

            return dst_id, target_x, target_y, dst_edge

        return None

    def _has_intra_binding_between(
        self, src_monitor_id: int, dst_monitor_id: int
    ) -> bool:
        """True iff the workspace authorises a ``src -> dst`` cross-monitor transition."""
        return (src_monitor_id, dst_monitor_id) in self._intra_pairs

    @staticmethod
    def _infer_exit_edge(previous, x: float, y: float) -> Optional[ScreenEdge]:
        """Infer which edge of ``previous`` the cursor crossed to reach (x, y).

        When the OS warps the cursor across physically-adjacent monitors
        in a single tick, the edge detector never fires on ``previous``.
        Reconstructing the exit edge from the cursor's offset lets the
        drift handler still resolve a workspace edge binding before
        falling back to a clamp.

        Boundary convention: "cursor is already OUTSIDE ``previous``" -
        ``x < min_x`` / ``x >= max_x`` (the box is half-open ``[min,
        max)``, so ``max`` itself is outside). This is intentionally NOT
        the same as :meth:`_detect_edge_via_delta` /
        :meth:`_detect_directed_edge` ("at the inner edge, still inside",
        ``x >= max-1``): this helper only runs after the OS has already
        moved the cursor onto an adjacent monitor, so ``x == min_x`` is
        still inside ``previous`` and correctly returns no edge here.
        """
        if previous is None:
            return None
        if x < previous.min_x:
            return ScreenEdge.LEFT
        if x >= previous.max_x:
            return ScreenEdge.RIGHT
        if y < previous.min_y:
            return ScreenEdge.TOP
        if y >= previous.max_y:
            return ScreenEdge.BOTTOM
        return None

    def _lookup_return_to_server(
        self,
        monitor,
        edge: ScreenEdge,
        x: float,
        y: float,
    ) -> Optional[tuple[float, float]]:
        """Edge binding lookup against an explicit (monitor, edge, x, y).

        Used by the drift handler when cursor has already crossed off
        ``monitor`` in the OS, so ``find_monitor_at(x, y)`` would return
        the WRONG monitor for the lookup.
        """
        if not self._edge_bindings or monitor is None or self._server_bbox is None:
            return None
        edge_str = self._EDGE_TO_STRING_CLIENT.get(edge)
        if edge_str is None:
            return None

        m_w = max(1, monitor.max_x - monitor.min_x)
        m_h = max(1, monitor.max_y - monitor.min_y)
        if edge == ScreenEdge.LEFT or edge == ScreenEdge.RIGHT:
            axis_norm = (y - monitor.min_y) / m_h
        else:
            axis_norm = (x - monitor.min_x) / m_w
        axis_norm = max(0.0, min(1.0, axis_norm))

        for b in self._edge_bindings:
            if b.get("client_monitor_id") != monitor.monitor_id:
                continue
            if b.get("client_edge") != edge_str:
                continue
            c_start = b.get("client_axis_start", 0.0)
            c_end = b.get("client_axis_end", 0.0)
            c_span = c_end - c_start
            if c_span <= 0 or not (c_start <= axis_norm < c_end):
                continue

            local_norm = (axis_norm - c_start) / c_span
            s_start = b.get("server_axis_start", 0.0)
            s_end = b.get("server_axis_end", 0.0)
            server_axis = s_start + local_norm * (s_end - s_start)

            s_min_x = int(b.get("server_monitor_min_x", 0))
            s_min_y = int(b.get("server_monitor_min_y", 0))
            s_max_x = int(b.get("server_monitor_max_x", 0))
            s_max_y = int(b.get("server_monitor_max_y", 0))
            s_edge = b.get("server_edge")
            s_w = max(1, s_max_x - s_min_x)
            s_h = max(1, s_max_y - s_min_y)
            edge_margin = 6
            if s_edge == "right":
                target_x = s_max_x - edge_margin
                target_y = s_min_y + server_axis * s_h
            elif s_edge == "left":
                target_x = s_min_x + edge_margin
                target_y = s_min_y + server_axis * s_h
            elif s_edge == "bottom":
                target_x = s_min_x + server_axis * s_w
                target_y = s_max_y - edge_margin
            elif s_edge == "top":
                target_x = s_min_x + server_axis * s_w
                target_y = s_min_y + edge_margin
            else:
                continue

            bx0, by0, bx1, by1 = self._server_bbox
            bw = max(1, bx1 - bx0)
            bh = max(1, by1 - by0)
            x_norm = (target_x - bx0) / bw
            y_norm = (target_y - by0) / bh
            if x_norm < 0.0:
                x_norm = 0.0
            elif x_norm > 1.0:
                x_norm = 1.0
            if y_norm < 0.0:
                y_norm = 0.0
            elif y_norm > 1.0:
                y_norm = 1.0
            return x_norm, y_norm

        return None

    def _clamp_cursor_to_monitor(self, monitor) -> None:
        """Bring the cursor back INSIDE ``monitor`` when it has actually left it.

        Only genuinely out-of-bounds positions are moved, and only onto the
        nearest valid pixel. Sitting *on* the boundary pixel is not "outside":
        the OS already holds the cursor at the desktop bound, so nudging it a
        pixel inward there would fight the system - the user pushes, the OS
        pins at the bound, we warp inward, every tick - which is visible as the
        cursor bouncing off the edge. Whatever we need to know about the edge
        we compute from the position; we don't restate it by moving the cursor.

        What is left to this method is the case the OS does *not* handle: a
        cursor that drifted onto another monitor of this client, or into an
        L-shaped dead zone (see the ``_handle_os_drift`` callers).
        """
        if monitor is None:
            return
        try:
            pos = self._cursor_position()
            if pos is None:
                return
            cx, cy = pos
            new_x = max(monitor.min_x, min(monitor.max_x - 1, cx))
            new_y = max(monitor.min_y, min(monitor.max_y - 1, cy))
            if (new_x, new_y) != (cx, cy):
                self._warp_cursor(new_x, new_y)
        except Exception as e:
            self._logger.error("failed to clamp cursor to monitor", error=str(e))

    def _resolve_return_to_server(
        self,
        edge: ScreenEdge,
        x: float,
        y: float,
    ) -> Optional[tuple[float, float]]:
        """Dual of ``ServerMouseListener._resolve_cross_screen_target``."""
        monitor = self._monitor_layout.nearest_monitor(x, y)
        if monitor is None:
            return None
        return self._lookup_return_to_server(monitor, edge, x, y)

    async def _mouse_event_callback(self, message):
        try:
            if not self._running:
                await self.start()

            if self._cross_screen_event.is_set() or not self._is_active:
                return await asyncio.sleep(0)

            await self._queue.put(message)
            return None
        except Exception as e:
            self._logger.error("failed to process mouse event", error=str(e))
            return None

    async def _check_edge(self):
        """Enforce the workspace topology on every cursor tick."""
        if (
            self._checking_edge
            or self._cross_screen_event.is_set()
            or not self._is_active
        ):
            return await asyncio.sleep(0)

        try:
            async with self._edge_check_lock:
                if self._cross_screen_event.is_set() or not self._is_active:
                    return await asyncio.sleep(0)

                self._checking_edge = True

                pos = self._cursor_position()
                if pos is None:
                    return None
                x, y = pos
                self._movement_history.append((x, y))

                current_monitor = self._find_monitor_for_cursor(x, y)
                if current_monitor is not None:
                    self._active_target_bbox = (
                        current_monitor.min_x,
                        current_monitor.min_y,
                        current_monitor.max_x,
                        current_monitor.max_y,
                    )
                    self._active_monitor_id = current_monitor.monitor_id

                if await self._handle_os_drift(x, y, current_monitor):
                    return None

                if current_monitor is not None:
                    self._last_known_monitor_id = current_monitor.monitor_id

                edge = None
                if len(self._movement_history) >= self.MOVEMENT_HISTORY_N_THRESHOLD:
                    edge = self._detect_directed_edge(
                        movement_history=self._movement_history,
                        x=x,
                        y=y,
                        monitor=current_monitor,
                    )
                if edge is None:
                    edge = self._detect_edge_via_delta(x, y, current_monitor)

                if edge is None or self._is_dragging:
                    return None

                # Gate return-to-server through the entry edge on the lag-free
                # ``_inward_travel`` (see ``_accumulate_inward_travel``), NOT the
                # laggy OS read-back that drives ``edge``: allow it only once the
                # cursor has genuinely entered (armed) AND has come back to
                # within ``RETURN_RELEASE_MARGIN`` of the edge. This kills both
                # the at-landing jitter and the fast-motion bounce (cursor far
                # inside while the read-back still says the edge). Returns
                # through any OTHER edge fire immediately. The OS-drift path
                # (``_handle_os_drift`` above) and intra-client warps below are
                # intentionally NOT gated - drift is a real OS transition and
                # warps route within this client, not back to the server.
                entry_gate_open = edge != self._return_locked_edge or (
                    self._return_armed
                    and self._inward_travel <= self.RETURN_RELEASE_MARGIN
                )
                if entry_gate_open and await self._try_return_to_server(edge, x, y):
                    return await asyncio.sleep(0)

                if self._try_intra_client_warp_sync(edge, x, y, current_monitor):
                    return None

                if current_monitor is not None:
                    self._clamp_cursor_to_monitor(current_monitor)
                    self._movement_history.clear()
                return None

        except Exception as e:
            self._logger.error("failed to dispatch screen event", error=str(e))
        finally:
            self._checking_edge = False

    async def _handle_os_drift(self, x: float, y: float, current_monitor) -> bool:
        """Detect/consume unauthorised OS-driven monitor transitions.

        ``True`` ⇒ drift was handled (caller must return). The fast-motion
        case bypasses ``_detect_directed_edge`` so the exit edge has to be
        re-inferred to honour workspace return-to-server/intra-warp bindings.
        """
        if not (
            current_monitor is not None
            and self._last_known_monitor_id is not None
            and current_monitor.monitor_id != self._last_known_monitor_id
            and not self._has_intra_binding_between(
                self._last_known_monitor_id, current_monitor.monitor_id
            )
            and not self._is_dragging
        ):
            return False

        previous = next(
            (
                m
                for m in self._monitor_layout.monitors
                if m.monitor_id == self._last_known_monitor_id
            ),
            None,
        )
        if previous is None:
            return False

        exit_edge = self._infer_exit_edge(previous, x, y)
        self._logger.debug(
            "OS drift detected",
            from_monitor=previous.monitor_id,
            to_monitor=current_monitor.monitor_id,
            exit_edge=str(exit_edge) if exit_edge else None,
            bindings=len(self._edge_bindings),
            has_server_bbox=self._server_bbox is not None,
        )

        if exit_edge is not None:
            edge_x = max(previous.min_x, min(previous.max_x - 1, x))
            edge_y = max(previous.min_y, min(previous.max_y - 1, y))

            resolved = self._lookup_return_to_server(
                previous, exit_edge, edge_x, edge_y
            )
            if resolved is not None:
                target_x, target_y = resolved
                # Pull the local cursor back inside ``previous`` so it
                # doesn't visually remain stranded on the unplaced
                # OS-neighbour after handing control back to the server.
                self._clamp_cursor_to_monitor(previous)
                self._logger.debug(
                    "return-to-server from drift",
                    monitor=previous.monitor_id,
                    edge=str(exit_edge),
                    target_x=target_x,
                    target_y=target_y,
                )
                await self._force_return_to_server(target_x, target_y)
                return True

            self._logger.debug(
                "no return-to-server binding for drift edge",
                monitor=previous.monitor_id,
                edge=str(exit_edge),
            )
            if self._try_intra_client_warp_sync(exit_edge, edge_x, edge_y, previous):
                return True

        self._clamp_cursor_to_monitor(previous)
        pos = self._cursor_position()
        if pos is not None:
            nx, ny = pos
            self._movement_history.clear()
            self._movement_history.append((nx, ny))
            self._active_target_bbox = (
                previous.min_x,
                previous.min_y,
                previous.max_x,
                previous.max_y,
            )
            self._active_monitor_id = previous.monitor_id
        return True

    async def _try_return_to_server(self, edge: ScreenEdge, x: float, y: float) -> bool:
        """Return True if a cross-screen binding consumed the edge approach."""
        resolved = self._resolve_return_to_server(edge, x, y)
        if resolved is None:
            return False
        target_x, target_y = resolved
        await self._force_return_to_server(target_x, target_y)
        return True

    def _try_intra_client_warp_sync(
        self, edge: ScreenEdge, x: float, y: float, current_monitor
    ) -> bool:
        """Execute an intra-client warp if a binding covers the edge."""
        warp = self._resolve_intra_client_warp(edge, x, y, current_monitor)
        if warp is None:
            return False
        dst_monitor_id, target_x, target_y, dst_edge = warp
        try:
            self._warp_cursor(int(target_x), int(target_y))
        except Exception as e:
            self._logger.error("failed to warp cursor intra-client", error=str(e))
            return True
        # Update last-known to the warp destination so the next tick
        # doesn't flag this as drift.
        self._last_known_monitor_id = dst_monitor_id
        self._active_monitor_id = dst_monitor_id
        for m in self._monitor_layout.monitors:
            if m.monitor_id == dst_monitor_id:
                self._active_target_bbox = (
                    m.min_x,
                    m.min_y,
                    m.max_x,
                    m.max_y,
                )
                break
        self._movement_history.clear()
        # A warp parks the cursor just inside ``dst_edge`` - the same
        # on-the-edge situation as a fresh landing - so re-arm the
        # return lock against it until the cursor travels inward again.
        self._return_locked_edge = self._STRING_TO_EDGE_CLIENT.get(dst_edge)
        self._inward_travel = 0
        self._return_armed = False
        return True

    async def _position_cursor(self, x: float | int, y: float | int):
        """Position the cursor at normalised ``(x, y)`` over the active target bbox."""
        try:
            min_x, min_y, max_x, max_y = self._active_target_bbox
            width = max_x - min_x
            height = max_y - min_y
            if width <= 0 or height <= 0:
                return
            x = max(min_x, min(max_x - 1, round(min_x + x * width)))
            y = max(min_y, min(max_y - 1, round(min_y + y * height)))
        except ValueError:
            return

        try:
            self._warp_cursor(x, y)
            await asyncio.sleep(0)
        except Exception as e:
            self._logger.error("failed to position cursor", error=str(e))

    def _move_cursor(
        self, x: float | int, y: float | int, dx: float | int, dy: float | int
    ):
        # (-1, -1) signals relative movement; otherwise absolute over
        # ``_active_target_bbox``.
        if x == -1 and y == -1:
            try:
                dx = int(dx)
                dy = int(dy)
            except ValueError:
                dx = 0
                dy = 0

            # Cached so ``_check_edge`` can detect a push toward an edge
            # when OS clamping has stalled the position history. Stays the
            # RAW delta: it is a direction hint for ``_detect_edge_via_delta``,
            # not a measure of displacement.
            self._last_move_delta = (dx, dy)
            # Inject first, then advance the return lockout from what the
            # backend actually applied to the cursor - under a pointer lock
            # macOS pins the event, so the raw delta would credit travel the
            # cursor never made, saturate ``_inward_travel`` and latch
            # ``_return_armed`` (see ``_accumulate_inward_travel``).
            applied = self._inject_relative(dx, dy)
            self._accumulate_inward_travel(*applied)
        else:
            try:
                min_x, min_y, max_x, max_y = self._active_target_bbox
                width = max_x - min_x
                height = max_y - min_y
                if width <= 0 or height <= 0:
                    return
                x = max(min_x, min(max_x - 1, round(min_x + x * width)))
                y = max(min_y, min(max_y - 1, round(min_y + y * height)))
            except ValueError:
                return

            try:
                self._warp_cursor(x, y)
            except Exception as e:
                self._logger.error("failed to position cursor", error=str(e))

    def _on_relative_injection_degraded(self) -> None:
        """Hook: give a degraded injection backend one chance to recover.

        Called on activation only - never from the move path - so a backend
        whose native injection failed transiently (at daemon start, at the login
        window) isn't pinned to its fallback for the whole session. No-op by
        default; macOS uses it to re-arm the HID path.
        """
        return None

    def _motion_bounds(self, x: float, y: float) -> tuple[int, int, int, int]:
        """The box the cursor can move in from ``(x, y)``.

        The monitor under the cursor when there is one - a taller neighbour
        makes the desktop union bigger than where the cursor can actually go -
        else the whole virtual desktop.
        """
        monitor = self._find_monitor_for_cursor(x, y)
        if monitor is not None:
            return (monitor.min_x, monitor.min_y, monitor.max_x, monitor.max_y)
        return self._screen_bbox

    def _motion_is_bounded(self, x: float, y: float, dx: int, dy: int) -> bool:
        """True when the OS is expected to swallow this delta.

        A cursor already against a bound does not move when pushed further that
        way: the displacement is zero for a reason we can *see*, unlike a
        foreground app holding the pointer. Backends that measure the applied
        displacement use this to keep the two apart - counting the geometric
        case as a held pointer would suspend edge routing exactly while the user
        is pushing at the edge to hand control back to the server.
        """
        min_x, min_y, max_x, max_y = self._motion_bounds(x, y)

        def blocked(pos: float, delta: int, low: int, high: int) -> bool:
            if delta == 0:
                return True  # nothing was requested on this axis
            return (delta < 0 and pos <= low) or (delta > 0 and pos >= high - 1)

        return blocked(x, dx, min_x, max_x) and blocked(y, dy, min_y, max_y)

    def _cursor_position(self) -> Optional[tuple[float, float]]:
        """Current cursor position, or ``None`` when it can't be read.

        Every read on the client path goes through here so a backend can keep
        it in the same event space as its injection - see the macOS override,
        where mixing an AppKit-level read with an HID-level write is what used
        to make the cursor drift.
        """
        try:
            pos = self._controller.position
        except Exception as e:
            self._logger.error("failed to read cursor position", error=str(e))
            return None
        if pos is None or len(pos) != 2:
            return None
        return float(pos[0]), float(pos[1])

    def _warp_cursor(self, x: float | int, y: float | int) -> None:
        """Place the cursor at absolute ``(x, y)``.

        Counterpart of ``_cursor_position``: every absolute placement on the
        client path goes through here (landing, clamp, intra-client warp), which
        is also why the measured-displacement baseline is dropped here - a warp
        is not travel.
        """
        self._last_seen_pos = None
        self._immobile_moves = 0
        self._controller.position = (x, y)

    def _inject_relative(self, dx: int, dy: int) -> tuple[int, int]:
        """Inject a relative pointer motion of ``(dx, dy)``.

        Returns the displacement actually applied to the visible cursor,
        which is what ``_move_cursor`` feeds to ``_accumulate_inward_travel``
        - a backend whose motion the OS may withhold (macOS under a game's
        cursor grab) must report what really happened, or the return-lock
        offset drifts away from the real cursor position.

        The default implementation delegates to pynput's ``Controller.move``.
        On macOS and Windows pynput implements this as an *absolute* warp
        (read current position, set ``position = pos + delta``), which
        first-person games reading raw/relative HID movement do not see —
        only the visible cursor moves. Those backends override this hook with
        genuine relative motion: macOS through the HID system
        (``IOHIDPostEvent``, so a game's cursor grab is honoured by the OS),
        Windows through ``SendInput`` with ``MOUSEEVENTF_MOVE``. Linux/Wayland
        (libei) already moves relatively and uses this default.
        """
        self._controller.move(dx=dx, dy=dy)
        return (dx, dy)

    def _click(self, button: int | None, is_pressed: bool):
        """Forward press/release to the OS, tagging multi-click sequences.

        Consecutive presses on the same button within
        ``DOUBLE_CLICK_THRESHOLD`` increment ``click_count`` so the OS
        recognises double/triple clicks.
        """
        try:
            name = ButtonMapping(button).name
            btn = Button[name]
        except (ValueError, KeyError):
            return

        if is_pressed:
            # Release first if already pressed (duplicated/reordered
            # press) so we don't get stuck.
            if self._pressed:
                try:
                    self._controller.release(btn)
                except Exception as e:
                    self._logger.error("failed to release stuck button", error=str(e))
                self._pressed = False

            current_time = time()
            if (
                (current_time - self._last_press_time) < self.DOUBLE_CLICK_THRESHOLD
                and self._previous_button == button
                and self._click_count < self.MAX_CLICK_COUNT
            ):
                self._click_count += 1
            else:
                self._click_count = 1

            self._apply_click_count(self._click_count)

            try:
                self._controller.press(btn)
                self._pressed = True
            except Exception as e:
                self._logger.error("failed to press button", error=str(e))

            self._last_press_time = current_time
            self._previous_button = button
        else:
            if self._pressed:
                self._apply_click_count(self._click_count)
                try:
                    self._controller.release(btn)
                except Exception as e:
                    self._logger.error("failed to release button", error=str(e))
                self._pressed = False

        self._is_dragging = is_pressed and ButtonMapping(button).value in [
            ButtonMapping.left.value,
            ButtonMapping.right.value,
        ]

    def _apply_click_count(self, count: int):
        """Hint pynput's macOS controller about the desired click_count.

        Only meaningful on macOS where ``_press`` increments ``_click``
        and tags the Quartz event with ``kCGMouseEventClickState``.
        """
        if not hasattr(self._controller, "_click"):
            return
        try:
            # macOS _press does ``self._click += 1`` before posting, so
            # we set count-1 to land on the desired value.
            self._controller._click = max(count - 1, 0)
        except Exception:
            pass

    def _scroll(self, dx: int | float, dy: int | float):
        try:
            dx = int(dx)
            dy = int(dy)
        except ValueError:
            return

        self._controller.scroll(dx, dy)
