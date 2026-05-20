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
from threading import Event, Lock

from event import (
    BusEventType,
    MouseEvent,
    EventMapper,
    ClientTopologyCommandEvent,
    ClientTopologyUpdatedEvent,
    CrossScreenCommandEvent,
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

from utils.logging import get_logger, Logger
from utils.screen import Screen
from input.utils import ScreenEdge, EdgeDetector, ButtonMapping

from .backend import MouseListener, MouseController, Button, BACKEND


class ServerMouseListener(object):
    """Base class for server-side mouse listeners."""

    MOVEMENT_HISTORY_N_THRESHOLD = 6
    MOVEMENT_HISTORY_LEN = 8

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
        # Edge detection uses the full MonitorLayout so the outer edges
        # of EACH monitor count - asymmetric layouts where the primary's
        # edges are interior to the union bbox would otherwise miss
        # crossings.
        self._screen_size: tuple[int, int] = Screen.get_size()
        self._monitor_layout = Screen.get_monitor_layout()
        self._screen_bbox: tuple[int, int, int, int] = (
            self._monitor_layout.virtual_bbox
            if self._monitor_layout.monitors
            else Screen.get_virtual_bbox()
        )
        self._cross_screen_event = Event()
        self._cross_screen_lock = asyncio.Lock()
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

        self._logger = get_logger(self.__class__.__name__)

        self._logger.info(
            f"Mouse listener backend: {BACKEND.get('mouse_listener', 'unknown')}"
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

    async def _on_client_connected(self, data: Optional[ClientConnectedEvent]):
        if data is None:
            return

        client_uid = data.client_uid
        client_streams = data.streams
        if client_streams is None:
            await asyncio.sleep(0)
            return

        if client_uid and StreamType.MOUSE in client_streams:
            self._active_clients[client_uid] = True
            self._edge_bindings_by_client[client_uid] = list(
                getattr(data, "edge_bindings", []) or []
            )
            self._intra_bindings_by_client[client_uid] = list(
                getattr(data, "intra_client_bindings", []) or []
            )

        await asyncio.sleep(0)

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        if data is None:
            return

        client_uid = data.client_uid
        if client_uid and client_uid in self._active_clients:
            del self._active_clients[client_uid]
        self._edge_bindings_by_client.pop(client_uid, None)
        self._intra_bindings_by_client.pop(client_uid, None)

        if not self._active_clients:
            self._listening = False

        await asyncio.sleep(0)

    async def _on_client_layout_updated(self, data: Optional[ClientLayoutUpdatedEvent]):
        """Hot-swap a client's cached EdgeBindings after the layout
        editor saves, so changes take effect on the next crossing."""
        if data is None or not data.client_uid:
            return
        if data.client_uid in self._active_clients:
            self._edge_bindings_by_client[data.client_uid] = list(
                data.edge_bindings or []
            )
            self._intra_bindings_by_client[data.client_uid] = list(
                getattr(data, "intra_client_bindings", []) or []
            )
        await asyncio.sleep(0)

    async def _on_hotkey_directional(
        self, data: Optional[ScreenSwitchDirectionalRequestEvent]
    ):
        """Resolve a directional hotkey to an adjacent screen via the layout topology."""
        if data is None:
            return

        # Anchor for the spatial resolver: when on a client the OS
        # position is stale (server's last position before the crossing),
        # so we always start from the cached ``_last_server_cursor_pos``
        # and only refresh it from the controller when on the server.
        if self._listening:
            x, y = self._last_server_cursor_pos
        else:
            x, y = self._last_server_cursor_pos
            try:
                from input.mouse.backend import MouseController

                pos = MouseController().position
                if pos and len(pos) == 2:
                    x, y = float(pos[0]), float(pos[1])
                    self._last_server_cursor_pos = (x, y)
            except Exception as e:
                self._logger.debug(
                    f"MouseController().position failed in hotkey resolver, "
                    f"using last tracked cursor position ({e})"
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
            self._logger.error(f"Error during hotkey directional switch ({e})")

    async def _on_hotkey_cycle(self, data: Optional[ScreenSwitchCycleRequestEvent]):
        if data is None:
            return
        uids = list(self._active_clients.keys())
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
            self._logger.error(f"Error during hotkey cycle switch ({e})")

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        if data is None:
            return

        active_screen = data.active_screen

        if active_screen is not None:
            with self._server_state_lock:
                self._movement_history.clear()
            self._listening = True
            self._active_client_uid = active_screen
            self._cross_screen_event.clear()
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

        await asyncio.sleep(0)

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

        Returns ``(client_uid, binding_dict, server_axis_norm)`` or
        ``None`` if no binding covers ``(server_monitor, edge,
        axis_norm)``. When the cursor sits in a dead-zone gap of an
        L-shape layout we snap to the nearest monitor so a corner
        crossing still resolves.
        """
        edge_str = self._EDGE_TO_STRING.get(edge)
        if not edge_str or not self._edge_bindings_by_client:
            return None

        monitor = self._monitor_layout.find_monitor_at(cursor_x, cursor_y)
        if monitor is None and self._monitor_layout.monitors:
            best = None
            best_dist = None
            for m in self._monitor_layout.monitors:
                cx = max(m.min_x, min(cursor_x, m.max_x - 1))
                cy = max(m.min_y, min(cursor_y, m.max_y - 1))
                dist = (cx - cursor_x) ** 2 + (cy - cursor_y) ** 2
                if best_dist is None or dist < best_dist:
                    best = m
                    best_dist = dist
            monitor = best
        if monitor is None:
            return None

        m_w = max(1, monitor.max_x - monitor.min_x)
        m_h = max(1, monitor.max_y - monitor.min_y)
        if edge == ScreenEdge.LEFT or edge == ScreenEdge.RIGHT:
            axis_norm = (cursor_y - monitor.min_y) / m_h
        else:
            axis_norm = (cursor_x - monitor.min_x) / m_w
        axis_norm = max(0.0, min(1.0, axis_norm))

        for client_uid, bindings in self._edge_bindings_by_client.items():
            for b in bindings:
                if b.get("server_monitor_id") != monitor.monitor_id:
                    continue
                if b.get("server_edge") != edge_str:
                    continue
                s_start = b.get("server_axis_start", 0.0)
                s_end = b.get("server_axis_end", 0.0)
                if s_start <= axis_norm < s_end:
                    return client_uid, b, axis_norm

        return None

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
        return list(self._active_clients.keys())

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
            if self._cross_screen_event.is_set() or self._handling_cross_screen:
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

                try:
                    self._cross_screen_event.set()
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
                    self._schedule_async(
                        self._handle_cross_screen(
                            edge, mouse_event, target_screen, target_monitor_id
                        )
                    )
                except Exception as e:
                    self._logger.error(f"Failed to dispatch mouse event ({e})")
                finally:
                    self._cross_screen_event.clear()

        return True

    def _schedule_async(self, coro):
        """Schedule an async coroutine from the pynput thread."""
        if self._loop is not None and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(coro, self._loop)
                return
            except Exception as e:
                self._logger.error(f"Error scheduling coroutine ({e})")

        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(coro, loop)
                else:
                    self._logger.warning(
                        "Event loop not running. Cannot schedule async operation"
                    )
            except Exception as e:
                self._logger.warning(
                    f"No event loop available for async operation ({e})"
                )

    async def _handle_cross_screen(
        self,
        edge: ScreenEdge,
        mouse_event: MouseEvent,
        screen: str,
        client_monitor_id: Optional[int] = None,
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
                    )
                )
                await self.stream.send(mouse_event)
                await asyncio.sleep(0)

        except Exception as e:
            self._logger.error(f"Error handling cross-screen ({e})")
        finally:
            with self._server_state_lock:
                self._handling_cross_screen = False
            self._cross_screen_event.clear()

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
                is_presed=pressed,
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
                self._logger.error(f"Failed to dispatch mouse click event ({e})")

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
                self._logger.error(f"Failed to dispatch mouse scroll event ({e})")
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
            f"Mouse controller backend: {BACKEND.get('mouse_controller', 'unknown')}"
        )

        self.event_bus.subscribe(
            event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
            callback=self._on_active_screen_changed,
        )

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        """Reposition the server cursor when control returns (active screen None)."""
        if data is not None:
            active_screen = data.active_screen
            if active_screen is None:
                x = data.x
                y = data.y
                if x > -1 and y > -1:
                    # Position multiple times so absolute placement converges
                    # across platforms.
                    for _ in range(50):
                        self.position_cursor(x, y)

        await asyncio.sleep(0)

    def position_cursor(self, x: float | int, y: float | int):
        """Position the cursor at normalised ``(x, y)``, denormalised over the virtual desktop bbox."""
        try:
            min_x, min_y, max_x, max_y = self._screen_bbox
            width = max_x - min_x
            height = max_y - min_y
            if width <= 0 or height <= 0:
                return
            x = max(min_x, min(max_x - 1, round(min_x + x * width)))
            y = max(min_y, min(max_y - 1, round(min_y + y * height)))
        except ValueError:
            self._logger.log(f"Invalid x or y values: x={x}, y={y}", Logger.ERROR)
            return

        try:
            self._controller.position = (x, y)
        except Exception as e:
            self._logger.log(f"Failed to position cursor ({e})", Logger.ERROR)


class ClientMouseController(object):
    """Async client-side mouse controller (movements, clicks, scrolls)."""

    MOVEMENT_HISTORY_N_THRESHOLD = 6
    MOVEMENT_HISTORY_LEN = 8
    # Consecutive presses on the same button within this window are
    # tagged as a multi-click sequence (double, triple, ...).
    DOUBLE_CLICK_THRESHOLD = 0.4
    MAX_CLICK_COUNT = 10

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

        self._logger = get_logger(self.__class__.__name__)

        self._logger.info(
            f"Mouse controller backend: {BACKEND.get('mouse_controller', 'unknown')}"
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

    def check_cursor_validity(self) -> bool:
        """Ensure a cursor is available - may fail on Windows when no cursor is present."""
        try:
            pos = self._controller.position
            if pos is None or len(pos) != 2:
                return False
            return True
        except Exception:
            self._logger.log("Cursor not available.", Logger.ERROR)
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
            self._logger.log("Async worker started.", Logger.DEBUG)
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

            self._logger.log("Async worker stopped.", Logger.DEBUG)

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
                    self._move_cursor(event.x, event.y, event.dx, event.dy)
                    await self._check_edge()
                elif event.action == MouseEvent.POSITION_ACTION:
                    # Position multiple times so absolute placement
                    # converges across platforms.
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
                self._logger.log(f"Error in worker ({e})", Logger.ERROR)
                await asyncio.sleep(0.01)

    async def _on_client_active(self, data: Optional[ClientActiveEvent]):
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

        self._is_active = True
        self._cross_screen_event.clear()

        if not self._running:
            await self.start()

        # Position at the landing point if the server packed the coords
        # into CLIENT_ACTIVE. Done AFTER ``_is_active`` flips True so
        # the parallel POSITION_ACTION on the mouse stream can't race
        # the activation event and silently drop the landing.
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
        if not self._monitor_layout.monitors:
            return None
        m = self._monitor_layout.find_monitor_at(x, y)
        if m is not None:
            self._cached_monitor = m
            return m
        best = None
        best_dist = None
        for cand in self._monitor_layout.monitors:
            cx = max(cand.min_x, min(x, cand.max_x - 1))
            cy = max(cand.min_y, min(y, cand.max_y - 1))
            dist = (cx - x) ** 2 + (cy - y) ** 2
            if best_dist is None or dist < best_dist:
                best = cand
                best_dist = dist
        if best is not None:
            self._cached_monitor = best
        return best

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
    ) -> Optional[tuple[int, float, float]]:
        """Match an edge approach against an intra-client binding.

        Returns ``(dst_monitor_id, target_x, target_y)`` or ``None`` when
        no binding covers ``(monitor, edge, axis_norm)``.
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

            return dst_id, target_x, target_y

        return None

    def _has_intra_binding_between(
        self, src_monitor_id: int, dst_monitor_id: int
    ) -> bool:
        """True iff the workspace authorises a ``src → dst`` cross-monitor transition."""
        return (src_monitor_id, dst_monitor_id) in self._intra_pairs

    @staticmethod
    def _infer_exit_edge(
        previous, x: float, y: float
    ) -> Optional[ScreenEdge]:
        """Infer which edge of ``previous`` the cursor crossed to reach (x, y).

        When the OS warps the cursor across physically-adjacent monitors
        in a single tick, the edge detector never fires on ``previous``.
        Reconstructing the exit edge from the cursor's offset lets the
        drift handler still resolve a workspace edge binding before
        falling back to a clamp.
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
        if (
            not self._edge_bindings
            or monitor is None
            or self._server_bbox is None
        ):
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
        """Pin the cursor just inside ``monitor``'s bbox."""
        if monitor is None:
            return
        try:
            pos = self._controller.position
            if not pos or len(pos) != 2:
                return
            cx, cy = pos
            new_x = max(monitor.min_x + 1, min(monitor.max_x - 2, cx))
            new_y = max(monitor.min_y + 1, min(monitor.max_y - 2, cy))
            if (new_x, new_y) != (cx, cy):
                self._controller.position = (new_x, new_y)
        except Exception as e:
            self._logger.log(f"Failed to clamp cursor to monitor ({e})", Logger.ERROR)

    def _resolve_return_to_server(
        self,
        edge: ScreenEdge,
        x: float,
        y: float,
    ) -> Optional[tuple[float, float]]:
        """Dual of ``ServerMouseListener._resolve_cross_screen_target``.

        Translates a client-side edge crossing to the matching
        server-bbox-normalised position. ``None`` when no binding
        matches - caller stays on the client.
        """
        if not self._monitor_layout.monitors:
            return None

        monitor = self._monitor_layout.find_monitor_at(x, y)
        if monitor is None:
            best = None
            best_dist = None
            for m in self._monitor_layout.monitors:
                cx = max(m.min_x, min(x, m.max_x - 1))
                cy = max(m.min_y, min(y, m.max_y - 1))
                dist = (cx - x) ** 2 + (cy - y) ** 2
                if best_dist is None or dist < best_dist:
                    best = m
                    best_dist = dist
            monitor = best

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
            self._logger.log(f"Failed to process mouse event ({e})", Logger.ERROR)
            return None

    async def _check_edge(self):
        """Enforce the workspace topology on every cursor tick.

        Two jobs, in order:
        1. revert OS-driven monitor transitions not authorised by an
           intra-client binding (clamp back);
        2. resolve edge approaches: cross-screen → return-to-server,
           intra-client → explicit warp, otherwise clamp inside.

        Single-monitor / pre-topology clients have empty bindings and
        only see the cross-screen lookup + edge clamp - same behaviour
        as before.
        """
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

                pos = self._controller.position
                if pos is None or len(pos) != 2:
                    return None
                x, y = pos

                self._movement_history.append((x, y))

                # Keep ``_active_target_bbox`` in sync so an incoming
                # POSITION_ACTION denormalises against the right screen
                # even if the cursor moved between monitors.
                current_monitor = self._find_monitor_for_cursor(x, y)
                if current_monitor is not None:
                    self._active_target_bbox = (
                        current_monitor.min_x,
                        current_monitor.min_y,
                        current_monitor.max_x,
                        current_monitor.max_y,
                    )
                    self._active_monitor_id = current_monitor.monitor_id

                # 1) revert unauthorised OS drift between monitors
                if (
                    current_monitor is not None
                    and self._last_known_monitor_id is not None
                    and current_monitor.monitor_id != self._last_known_monitor_id
                    and not self._has_intra_binding_between(
                        self._last_known_monitor_id, current_monitor.monitor_id
                    )
                    and not self._is_dragging
                ):
                    previous = None
                    for m in self._monitor_layout.monitors:
                        if m.monitor_id == self._last_known_monitor_id:
                            previous = m
                            break
                    if previous is not None:
                        # Fast-motion case: the cursor crossed off
                        # ``previous`` so fast that ``_detect_directed_edge``
                        # never fired on it. If the exit edge has a
                        # workspace binding back to the server, honour
                        # it before falling back to the clamp - otherwise
                        # this client monitor becomes a one-way trap when
                        # its only return path shares an OS-adjacency
                        # with another, non-workspace-adjacent monitor.
                        exit_edge = self._infer_exit_edge(previous, x, y)
                        self._logger.debug(
                            f"OS drift detected from monitor "
                            f"{previous.monitor_id} -> {current_monitor.monitor_id}; "
                            f"exit_edge={exit_edge}; bindings={len(self._edge_bindings)}; "
                            f"server_bbox={self._server_bbox is not None}"
                        )
                        if exit_edge is not None:
                            edge_x = max(
                                previous.min_x, min(previous.max_x - 1, x)
                            )
                            edge_y = max(
                                previous.min_y, min(previous.max_y - 1, y)
                            )
                            resolved = self._lookup_return_to_server(
                                previous, exit_edge, edge_x, edge_y
                            )
                            if resolved is not None:
                                target_x, target_y = resolved

                                self._cross_screen_event.set()
                                self._movement_history.clear()

                                # Pull the local cursor back inside
                                # ``previous`` so it doesn't visually
                                # remain stranded on the unplaced
                                # OS-neighbour after we hand control
                                # back to the server.
                                self._clamp_cursor_to_monitor(previous)

                                self._logger.debug(
                                    f"Firing return-to-server from drift "
                                    f"(monitor {previous.monitor_id} {exit_edge}); "
                                    f"target=({target_x:.3f}, {target_y:.3f})"
                                )
                                command = CrossScreenCommandEvent(
                                    x=target_x, y=target_y
                                )
                                await self.command_stream.send(command)
                                await self.event_bus.dispatch(
                                    event_type=BusEventType.CLIENT_INACTIVE,
                                    data=None,
                                )
                                return await asyncio.sleep(0)
                            else:
                                self._logger.debug(
                                    f"No return-to-server binding for "
                                    f"monitor {previous.monitor_id} edge "
                                    f"{exit_edge}; falling through"
                                )

                            # No return-to-server; check whether the
                            # exit edge has an intra-client warp. If it
                            # does, honour the workspace destination
                            # instead of the OS one (the OS picked an
                            # unauthorised neighbour, but the workspace
                            # has a different valid neighbour for this
                            # edge).
                            warp = self._resolve_intra_client_warp(
                                exit_edge, edge_x, edge_y, previous
                            )
                            if warp is not None:
                                dst_monitor_id, target_x, target_y = warp
                                try:
                                    self._controller.position = (
                                        int(target_x),
                                        int(target_y),
                                    )
                                except Exception as e:
                                    self._logger.log(
                                        f"Failed to warp cursor intra-client "
                                        f"after drift ({e})",
                                        Logger.ERROR,
                                    )
                                else:
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
                                return None

                        self._clamp_cursor_to_monitor(previous)
                        pos = self._controller.position
                        if pos and len(pos) == 2:
                            x, y = pos
                            self._movement_history.clear()
                            self._movement_history.append((x, y))
                            self._active_target_bbox = (
                                previous.min_x,
                                previous.min_y,
                                previous.max_x,
                                previous.max_y,
                            )
                            self._active_monitor_id = previous.monitor_id
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

                # Delta fallback when OS clamping stalls the position
                # history against a monitor bound.
                if edge is None:
                    edge = self._detect_edge_via_delta(x, y, current_monitor)

                if edge is None or self._is_dragging:
                    return None

                # 2a) cross-screen binding -> return-to-server
                resolved = self._resolve_return_to_server(edge, x, y)
                if resolved is not None:
                    target_x, target_y = resolved

                    self._cross_screen_event.set()
                    self._movement_history.clear()

                    command = CrossScreenCommandEvent(x=target_x, y=target_y)
                    await self.command_stream.send(command)
                    await self.event_bus.dispatch(
                        event_type=BusEventType.CLIENT_INACTIVE, data=None
                    )
                    return await asyncio.sleep(0)

                # 2b) intra-client binding -> explicit warp
                warp = self._resolve_intra_client_warp(edge, x, y, current_monitor)
                if warp is not None:
                    dst_monitor_id, target_x, target_y = warp
                    try:
                        self._controller.position = (
                            int(target_x),
                            int(target_y),
                        )
                    except Exception as e:
                        self._logger.log(
                            f"Failed to warp cursor intra-client ({e})", Logger.ERROR
                        )
                        return None
                    # Update last-known to the warp destination so the
                    # next tick doesn't flag this as drift.
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
                    return None

                # 2c) void workspace edge -> clamp inside current monitor
                if current_monitor is not None:
                    self._clamp_cursor_to_monitor(current_monitor)
                    self._movement_history.clear()
                return None

        except Exception as e:
            self._logger.log(f"Failed to dispatch screen event ({e})", Logger.ERROR)
        finally:
            self._checking_edge = False

        return await asyncio.sleep(0)

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
            self._controller.position = (x, y)
            await asyncio.sleep(0)
        except Exception as e:
            self._logger.log(f"Failed to position cursor ({e})", Logger.ERROR)

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
            # when OS clamping has stalled the position history.
            self._last_move_delta = (dx, dy)
            self._controller.move(dx=dx, dy=dy)
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
                self._controller.position = (x, y)
            except Exception as e:
                self._logger.log(f"Failed to position cursor ({e})", Logger.ERROR)

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
                    self._logger.log(
                        f"Failed to release stuck button ({e})", Logger.ERROR
                    )
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
                self._logger.log(f"Failed to press button ({e})", Logger.ERROR)

            self._last_press_time = current_time
            self._previous_button = button
        else:
            if self._pressed:
                self._apply_click_count(self._click_count)
                try:
                    self._controller.release(btn)
                except Exception as e:
                    self._logger.log(f"Failed to release button ({e})", Logger.ERROR)
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
