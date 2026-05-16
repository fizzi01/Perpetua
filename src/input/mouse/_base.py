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
)
from event.bus import EventBus
from model.client import ScreenPosition

from network.stream import StreamType
from network.stream.handler import StreamHandler

from utils.logging import get_logger, Logger
from utils.screen import Screen
from input.utils import ScreenEdge, EdgeDetector, ButtonMapping

from .backend import MouseListener, MouseController, Button, BACKEND


class ServerMouseListener(object):
    """
    Base class for server-side mouse listeners.
    Its main purpose is to listen to mouse events and dispatch them
    """

    MOVEMENT_HISTORY_N_THRESHOLD = 6
    MOVEMENT_HISTORY_LEN = 8

    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
        filtering: bool = True,
    ):
        """
        Initializes the server mouse listener.

        Args:
            event_bus (EventBus): The event bus for dispatching events.
            stream_handler (StreamHandler): The stream handler for mouse events.
            command_stream (StreamHandler): The stream handler for command events.
            filtering (bool): Whether to apply platform-specific mouse event filtering.
        """

        self.stream = stream_handler  # Should be a mouse stream
        self.command_stream = command_stream
        self.event_bus = event_bus

        self._listening = False
        self._active_screens = {}
        # Spatial cross-screen routing table populated by
        # ``_on_client_connected``: one entry per ``client_screen`` (the
        # client's identifier in the bus events), value is the list of
        # serialized EdgeBindings derived from that client's placements.
        # When an edge crossing fires we look up the binding owning
        # ``(server_monitor_id, edge, axis_norm)`` and prefer it over
        # the legacy ``_active_screens`` lookup so multi-monitor
        # placements drive routing.
        self._edge_bindings_by_client: dict[str, list[dict]] = {}
        # Mirror of ``_edge_bindings_by_client``: the same abutments
        # serialised from the CLIENT's perspective. Pushed to each
        # client via ``ClientTopologyCommandEvent`` so the client can
        # resolve its own return-to-server crossings spatially instead
        # of through the legacy ScreenPosition lookup.
        self._reverse_bindings_by_client: dict[str, list[dict]] = {}
        # Primary monitor size kept for backward compatibility / scroll
        # events. Edge detection uses the full MonitorLayout so the
        # outer edges of EACH monitor count (asymmetric multi-monitor
        # layouts where the primary's edges are interior to the union
        # bbox would otherwise miss crossings).
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
        # threading.Lock for state shared between the pynput listener thread
        # and the asyncio event loop (`_handling_cross_screen` flag and
        # `_movement_history` deque). Held only across O(1) operations; NEVER
        # held across an `await`.
        self._server_state_lock = Lock()
        self._button_pressed: set[int] = set()  # Track pressed buttons

        # Check platform to set appropriate mouse filter
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

        # Queue for mouse movements history to detect screen edge reaching
        self._movement_history = deque(maxlen=self.MOVEMENT_HISTORY_LEN)
        self._is_dragging = False

        self._logger = get_logger(self.__class__.__name__)

        self._logger.info(
            f"Mouse listener backend: {BACKEND.get('mouse_listener', 'unknown')}"
        )

        # Store event loop reference for thread-safe async scheduling
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running yet - will be set when start() is called
            self._loop = None

        # Subscribe with async callbacks
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

    def _create_listener(self) -> MouseListener:
        """
        Creates a new mouse listener instance.
        """
        return MouseListener(
            on_move=self.on_move,
            on_scroll=self.on_scroll,
            on_click=self.on_click,
            **self._filter_args,
        )

    def start(self) -> bool:
        """
        Starts the mouse listener.
        """
        # Always re-capture the running loop: a previous start() may have
        # cached a loop that has since been closed (e.g. between tests).
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._logger.warning(
                "No event loop running. "
                "Mouse listener must be started within an async context."
            )

        if not self.is_alive():
            # Screen.hide_icon()
            self._listener = self._create_listener()
            self._listener.start()
        self._logger.debug("Started.")
        return True

    LISTENER_JOIN_TIMEOUT = 2.0  # sec - cap how long stop() will wait

    def stop(self) -> bool:
        """
        Stops the mouse listener. ``pynput.Listener.stop`` returns
        immediately; we explicitly wait for the OS-level listener thread.
        """
        if self._listener is not None and self.is_alive():
            self._listener.stop()
            try:
                self._listener.join(timeout=self.LISTENER_JOIN_TIMEOUT)
            except RuntimeError:
                # join() raises if the thread never started; safe to ignore.
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
        """
        Async event handler for when a client connects.
        """
        if data is None:
            return

        client_screen = data.client_screen
        # We need this check in order to not dispatch cross-screen events to clients without mouse stream
        client_streams = data.streams  # We check if client has mouse stream enabled
        if client_streams is None:
            await asyncio.sleep(0)
            return

        if client_screen and StreamType.MOUSE in client_streams:  # If not, we ignore
            self._active_screens[client_screen] = True
            # Cache the spatial routing table for this client. Each
            # entry is a serialized EdgeBinding dict — we keep them as
            # dicts on the hot path to avoid an extra import on the
            # pynput thread.
            self._edge_bindings_by_client[client_screen] = list(
                getattr(data, "edge_bindings", []) or []
            )
            # Cache the dual (client-side) view so we can push it to
            # the client right before the next cross-screen activation.
            self._reverse_bindings_by_client[client_screen] = list(
                getattr(data, "reverse_bindings", []) or []
            )

        await asyncio.sleep(0)

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        """
        Async event handler for when a client disconnects.
        """
        if data is None:
            return

        # try to get client from data to remove from active screens
        client = data.client_screen
        if client and client in self._active_screens:
            del self._active_screens[client]
        # Drop the cached routing table for the disconnected client so
        # stale bindings never accidentally route a crossing into a
        # client that's no longer reachable.
        self._edge_bindings_by_client.pop(client, None)
        self._reverse_bindings_by_client.pop(client, None)

        # if active screens is empty, we stop listening
        if len(self._active_screens.items()) == 0:
            self._listening = False

        await asyncio.sleep(0)

    async def _on_client_layout_updated(self, data: Optional[ClientLayoutUpdatedEvent]):
        """Hot-swap the cached EdgeBindings of a client without waiting
        for it to reconnect. Called from ``Server.set_client_layout``
        after a successful save so the layout editor's changes take
        effect on the very next mouse crossing.
        """
        if data is None or not data.client_screen:
            return
        if data.client_screen in self._active_screens:
            self._edge_bindings_by_client[data.client_screen] = list(
                data.edge_bindings or []
            )
            self._reverse_bindings_by_client[data.client_screen] = list(
                getattr(data, "reverse_bindings", []) or []
            )
        await asyncio.sleep(0)

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        """
        Async event handler for when the active screen changes.
        """
        if data is None:
            return

        # If active screen is not none then we can start listening to mouse events
        active_screen = data.active_screen

        if active_screen is not None:
            with self._server_state_lock:
                self._movement_history.clear()
            self._listening = True
            self._cross_screen_event.clear()
        else:
            self._listening = False

        await asyncio.sleep(0)

    def _screen_size_valid(self) -> bool:
        """Guard against (0,0) or negative dimensions (display disconnected
        / not yet probed). Prevents ZeroDivisionError and inf/nan
        coordinates from propagating through the event stream.
        """
        return self._screen_size[0] > 0 and self._screen_size[1] > 0

    def _bbox_span(self) -> "tuple[int, int, int, int, int, int]":
        """Return ``(min_x, min_y, max_x, max_y, width, height)`` of the
        virtual desktop bbox. Width/height are guaranteed >= 1 so callers
        can divide without checking again.
        """
        min_x, min_y, max_x, max_y = self._screen_bbox
        return min_x, min_y, max_x, max_y, max(1, max_x - min_x), max(1, max_y - min_y)

    # Lookup tables for the ScreenEdge <-> Edge-string mapping used by
    # ``_resolve_cross_screen_target``. Done at class scope so the hot
    # path doesn't allocate a fresh dict per crossing.
    _EDGE_TO_STRING: dict = {
        ScreenEdge.LEFT: "left",
        ScreenEdge.RIGHT: "right",
        ScreenEdge.TOP: "top",
        ScreenEdge.BOTTOM: "bottom",
    }
    _EDGE_TO_LEGACY_POSITION: dict = {
        ScreenEdge.LEFT: ScreenPosition.LEFT,
        ScreenEdge.RIGHT: ScreenPosition.RIGHT,
        ScreenEdge.TOP: ScreenPosition.TOP,
        ScreenEdge.BOTTOM: ScreenPosition.BOTTOM,
    }

    def _resolve_cross_screen_target(
        self,
        edge: ScreenEdge,
        cursor_x: float,
        cursor_y: float,
        bbox_x_norm: float,
        bbox_y_norm: float,
    ) -> Optional[tuple[str, Optional[int]]]:
        """Pick the target ``(client_screen, client_monitor_id)`` for an
        edge crossing.

        Priority:

        1. **Spatial routing** via ``_edge_bindings_by_client``: locate
           the server monitor currently under the cursor, project the
           cursor onto that monitor's secondary axis, and pick the
           client whose :class:`EdgeBinding` owns the resulting
           ``(server_monitor_id, edge, axis_norm)`` tuple. Returns the
           matched binding's ``client_monitor_id`` so the receiving
           client can land the cursor on the right physical screen.

        2. **Legacy fallback**: if no binding matches, route via the
           old ``_active_screens[ScreenPosition.X]`` lookup so clients
           that haven't been positioned on the workspace yet keep
           working with their ``screen_position`` enum. ``client_monitor_id``
           is ``None`` in this path.

        Returns ``None`` when neither path applies (no target).
        """
        edge_str = self._EDGE_TO_STRING.get(edge)
        legacy_pos = self._EDGE_TO_LEGACY_POSITION.get(edge)

        if edge_str and self._edge_bindings_by_client:
            # Find the server monitor whose edge was hit. We use the
            # MonitorLayout's hit-test; if the cursor is in a dead
            # zone (L-shape gap), fall back to the closest monitor.
            monitor = self._monitor_layout.find_monitor_at(cursor_x, cursor_y)
            if monitor is None and self._monitor_layout.monitors:
                # Cursor at the outer bbox edge but not strictly inside
                # any monitor; snap to the closest one.
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

            if monitor is not None:
                m_w = max(1, monitor.max_x - monitor.min_x)
                m_h = max(1, monitor.max_y - monitor.min_y)
                if edge == ScreenEdge.LEFT or edge == ScreenEdge.RIGHT:
                    axis_norm = (cursor_y - monitor.min_y) / m_h
                else:
                    axis_norm = (cursor_x - monitor.min_x) / m_w
                axis_norm = max(0.0, min(1.0, axis_norm))

                for client_screen, bindings in self._edge_bindings_by_client.items():
                    for b in bindings:
                        if (
                            b.get("server_monitor_id") == monitor.monitor_id
                            and b.get("server_edge") == edge_str
                            and b.get("axis_start", 0.0)
                            <= axis_norm
                            < b.get("axis_end", 0.0)
                        ):
                            cm = b.get("client_monitor_id")
                            return client_screen, int(cm) if cm is not None else None

        # Legacy fallback: virtual-desktop normalized coordinate is
        # only used as a tie-breaker for logging; the actual decision
        # is whether _any_ client is registered at that side.
        _ = bbox_x_norm
        _ = bbox_y_norm
        if legacy_pos and self._active_screens.get(legacy_pos, False):
            return legacy_pos, None

        return None

    def _darwin_mouse_suppress_filter(self, event_type, event):
        raise NotImplementedError("Mouse suppress filter not implemented yet.")

    def _win32_mouse_suppress_filter(self, msg, data):
        raise NotImplementedError("Mouse suppress filter not implemented yet.")

    def on_move(self, x, y):
        """
        Event handler for mouse movement.
        While not listening, it needs to check if the cursor is reaching the screen edges.
        Since pynput runs in its own thread, we need to schedule async operations.
        """
        if not self._screen_size_valid():
            return True
        # Snapshot the cross-screen guard atomically under the shared state
        # lock - `_handling_cross_screen` is mutated by `_handle_cross_screen`
        # on the event loop, and concurrent moves must observe a consistent
        # value to avoid two simultaneous cross-screen handlers.
        with self._server_state_lock:
            if self._cross_screen_event.is_set() or self._handling_cross_screen:
                return True
            should_buffer = not self._listening
            if should_buffer:
                try:
                    self._movement_history.append((x, y))
                except Exception:
                    pass
            history_ready = (
                should_buffer
                and len(self._movement_history) >= self.MOVEMENT_HISTORY_N_THRESHOLD
            )

        # The border check has to take in account only when we are moving forward and not backward or staying still
        if not self._listening:
            if history_ready:
                # Per-monitor edge detection: each monitor's outer edge
                # (an edge with no neighbour at this orthogonal coord)
                # counts as a candidate crossing point. Necessary for
                # asymmetric layouts where the primary monitor's edges
                # are INTERIOR to the union bbox.
                edge = EdgeDetector.is_at_edge(
                    movement_history=self._movement_history,
                    x=x,
                    y=y,
                    screen_size=self._monitor_layout,
                    is_dragging=self._is_dragging,
                )
                if edge is None:
                    # No edge reached yet; nothing more to do for this event.
                    return True

                mouse_event = MouseEvent(x=x, y=y, action=MouseEvent.POSITION_ACTION)
                min_x, min_y, _max_x, _max_y, width, height = self._bbox_span()
                x_norm = (x - min_x) / width
                y_norm = (y - min_y) / height

                # Per-monitor routing using the placements coming from
                # each ClientObj. The target (server_monitor_id, edge,
                # axis_norm) is matched against the cached EdgeBindings;
                # a hit overrides the legacy ScreenPosition fallback.
                resolved = self._resolve_cross_screen_target(
                    edge=edge,
                    cursor_x=x,
                    cursor_y=y,
                    bbox_x_norm=x_norm,
                    bbox_y_norm=y_norm,
                )
                if resolved is None:
                    return True
                target_screen, target_monitor_id = resolved

                try:
                    self._cross_screen_event.set()
                    if edge == ScreenEdge.LEFT:
                        mouse_event.x = 1
                        mouse_event.y = y_norm
                    elif edge == ScreenEdge.RIGHT:
                        mouse_event.x = 0
                        mouse_event.y = y_norm
                    elif edge == ScreenEdge.TOP:
                        mouse_event.x = x_norm
                        mouse_event.y = 1
                    elif edge == ScreenEdge.BOTTOM:
                        mouse_event.x = x_norm
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
        """
        Helper to schedule async coroutines from sync context (pynput thread).
        Uses saved event loop reference for thread-safe scheduling.
        """
        if self._loop is not None and not self._loop.is_closed():
            # Best case: we have a valid loop reference
            try:
                asyncio.run_coroutine_threadsafe(coro, self._loop)
                return
            except Exception as e:
                self._logger.error(f"Error scheduling coroutine ({e})")

        # Fallback: try to get running loop
        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            # Last resort: try to get event loop (may not work from thread)
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
        """Async handler for cross-screen events"""
        # Mark the cross-screen handler as in-flight under the shared state
        # lock so concurrent `on_move` calls observe it. The flag is reset
        # in the finally block - also under the lock.
        with self._server_state_lock:
            self._handling_cross_screen = True
        try:
            # Acquire lock to serialize cross-screen handling
            async with self._cross_screen_lock:
                with self._server_state_lock:
                    history_len = len(self._movement_history)
                if (
                    history_len < self.MOVEMENT_HISTORY_N_THRESHOLD
                ):  # Check again because of async
                    return

                # Reset movement history under the shared state lock since
                # the pynput thread may concurrently append to it.
                with self._server_state_lock:
                    self._movement_history.clear()

                # We notify the system that an active screen change has occurred
                await self.event_bus.dispatch(
                    event_type=BusEventType.SCREEN_CHANGE_GUARD,  # We first notify the cursor guard (cursor handler)
                    data=ActiveScreenChangedEvent(active_screen=screen),
                )

                # Push the latest topology to the activating client so
                # it can resolve return-to-server crossings spatially.
                # The BidirectionalStreamHandler routes both this and
                # the CrossScreenCommandEvent below to the same active
                # client (set by the SCREEN_CHANGE_GUARD dispatch above).
                reverse_bindings = self._reverse_bindings_by_client.get(screen) or []
                if reverse_bindings:
                    await self.command_stream.send(
                        ClientTopologyCommandEvent(
                            target=screen,
                            reverse_bindings=reverse_bindings,
                            server_bbox=self._screen_bbox,
                        )
                    )

                # Wait for message dispatch to complete
                await self.command_stream.send(
                    CrossScreenCommandEvent(
                        target=screen, client_monitor_id=client_monitor_id
                    )
                )
                await self.stream.send(mouse_event)
                await asyncio.sleep(0)

        except Exception as e:
            self._logger.error(f"Error handling cross-screen ({e})")
        finally:
            # Reset states under the shared lock so the pynput thread sees a
            # consistent flag transition.
            with self._server_state_lock:
                self._handling_cross_screen = False
            self._cross_screen_event.clear()

    def on_click(self, x, y, button: Button, pressed):
        if self._listening:
            if not self._screen_size_valid():
                return True
            button = ButtonMapping[button.name].value
            # Normalize click coordinates over the virtual-desktop bbox so a
            # click on a secondary monitor doesn't end up mapped past the
            # primary's [0, 1] range.
            min_x, min_y, _max_x, _max_y, width, height = self._bbox_span()
            mouse_event = MouseEvent(
                x=(x - min_x) / width,
                y=(y - min_y) / height,
                button=button,
                action=MouseEvent.CLICK_ACTION,
                is_presed=pressed,
            )
            try:
                # Schedule async send
                if not pressed and button in self._button_pressed:
                    self._button_pressed.remove(button)
                elif pressed:
                    self._button_pressed.add(button)
                else:
                    return True  # Ignore this event

                self._schedule_async(self.stream.send(mouse_event))
            except Exception as e:
                self._logger.error(f"Failed to dispatch mouse click event ({e})")

        else:
            # Track dragging state to avoid edge crossing
            self._is_dragging = pressed and ButtonMapping[button.name].value in [
                ButtonMapping.left.value,
                ButtonMapping.right.value,
            ]
        return True

    def on_scroll(self, x, y, dx, dy):
        if self._listening:
            mouse_event = MouseEvent(dx=dx, dy=dy, action=MouseEvent.SCROLL_ACTION)
            try:
                # Schedule async send
                self._schedule_async(self.stream.send(mouse_event))
            except Exception as e:
                self._logger.error(f"Failed to dispatch mouse scroll event ({e})")
        return True


class ServerMouseController(object):
    """
    Base class for server-side mouse controllers.
    Its main purpose is to control the mouse cursor position
    when the active screen changes.
    """

    def __init__(self, event_bus: EventBus):
        """
        Initializes the server mouse controller.

        Args:
            event_bus (EventBus): The event bus for dispatching events.
        """
        self.event_bus = event_bus

        self._screen_size: tuple[int, int] = Screen.get_size()
        # bbox spans every connected monitor; used when the server has to
        # re-position its own cursor after a return-from-client event.
        self._screen_bbox: tuple[int, int, int, int] = Screen.get_virtual_bbox()

        # Screen.hide_icon()
        self._controller = MouseController()
        self._logger = get_logger(self.__class__.__name__)

        self._logger.info(
            f"Mouse controller backend: {BACKEND.get('mouse_controller', 'unknown')}"
        )

        # Register for active screen changed events to reposition the cursor
        self.event_bus.subscribe(
            event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
            callback=self._on_active_screen_changed,
        )

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        """
        Activate only when the active screen becomes None.
        """
        if data is not None:
            active_screen = data.active_screen
            if active_screen is None:
                # Get the cursor position from data if available
                x = data.x
                y = data.y
                if x > -1 and y > -1:
                    # We need to position the cursor multiple times to ensure it works across platforms
                    for _ in range(50):
                        self.position_cursor(x, y)

        await asyncio.sleep(0)

    def position_cursor(self, x: float | int, y: float | int):
        """
        Position the mouse cursor to the specified (x, y) coordinates.

        Denormalizes against the virtual-desktop bbox so the cursor can land
        on any monitor of a multi-display server (e.g. when control returns
        from a client and we need to put the cursor on the second monitor).
        """
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
            # On some platforms, positioning may fail when cursor misses
            self._logger.log(f"Failed to position cursor ({e})", Logger.ERROR)


# TODO: Optimize edge detection to avoid false positives during crossing
class ClientMouseController(object):
    """
    Async mouse controller for client side.
    Handles mouse movements, clicks, and scrolls based on received events.
    Converted from multiprocessing to fully async with asyncio tasks.
    """

    MOVEMENT_HISTORY_N_THRESHOLD = 6
    MOVEMENT_HISTORY_LEN = 8
    # Time window within which consecutive presses on the same button are
    # treated as part of a multi-click sequence (double, triple, ...).
    DOUBLE_CLICK_THRESHOLD = 0.4
    # Safety cap on the click_count we forward to the OS.
    MAX_CLICK_COUNT = 10

    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
    ):
        """
        Initializes the client mouse controller.

        Args:
            event_bus (EventBus): The event bus for dispatching events.
            stream_handler (StreamHandler): The stream handler for mouse events.
            command_stream (StreamHandler): The stream handler for command events.
        """
        self.stream = stream_handler  # Should be a mouse stream
        self.command_stream = command_stream  # Should be a command stream
        self.event_bus = event_bus
        self._cross_screen_event = asyncio.Event()
        self._edge_check_lock = asyncio.Lock()
        self._checking_edge = False

        self._is_active = False
        self._current_screen = None
        # Target monitor id signalled by the server's most recent
        # CrossScreenCommandEvent. ``None`` falls back to the virtual
        # desktop bbox below (legacy / single-monitor client).
        self._active_monitor_id: Optional[int] = None
        # Reverse topology pushed by the server (one entry per
        # client-monitor edge that abuts a server monitor). Used by
        # ``_check_edge`` to resolve return-to-server crossings
        # spatially. Empty list = legacy ``EdgeDetector.get_crossing_coords``
        # fallback driven by ``screen_position``.
        self._reverse_bindings: list[dict] = []
        # Server's virtual desktop bbox; the return-to-server (x, y)
        # is normalised over this so the server lands the cursor at
        # the right pixel.
        self._server_bbox: Optional[tuple[int, int, int, int]] = None
        self._screen_size: tuple[int, int] = Screen.get_size()
        # Full per-monitor layout for edge detection on the mirror-back
        # crossing path; the bbox below is derived from it and kept for
        # denormalisation (mapping incoming positions across the whole
        # virtual desktop instead of pinning them to the primary).
        self._monitor_layout = Screen.get_monitor_layout()
        self._screen_bbox: tuple[int, int, int, int] = (
            self._monitor_layout.virtual_bbox
            if self._monitor_layout.monitors
            else Screen.get_virtual_bbox()
        )
        # Screen.hide_icon()  # On macOs calling controller.position can spawn a dock icon...

        # Instead of creating a listener, we just check edge cases after a mouse move event is received
        # Using deque for better performance and async compatibility
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

        # Check for cursor validity
        if not self.check_cursor_validity():
            raise RuntimeError("No valid cursor found.")

        # Async queue instead of multiprocessing queue
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

        # Register to receive mouse events from the stream (async callback)
        self.stream.register_receive_callback(
            self._mouse_event_callback, message_type="mouse"
        )

        # Subscribe with async callbacks
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
        """
        We use this check to ensure a cursor is available (On windows may fail if no cursor)
        """
        try:
            pos = self._controller.position
            if pos is None or len(pos) != 2:
                return False
            return True
        except Exception:
            self._logger.log("Cursor not available.", Logger.ERROR)
            return False

    async def start(self):
        """
        Starts the async mouse controller worker task.
        """
        if not self._running:
            self._running = True
            # Clear queue
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                finally:
                    await asyncio.sleep(0)

            # Start worker task
            self._worker_task = asyncio.create_task(self._run_worker())
            self._logger.log("Async worker started.", Logger.DEBUG)
            await asyncio.sleep(0)

    async def stop(self):
        """
        Stops the async mouse controller worker task.
        """
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
        """
        Checks if the async mouse controller worker task is running.
        """
        return (
            self._running
            and self._worker_task is not None
            and not self._worker_task.done()
        )

    async def _run_worker(self):
        """
        Async worker task to handle mouse events.
        Replaces the multiprocessing worker.
        """
        while self._running:
            try:
                # Get message from async queue
                message = await self._queue.get()

                event = EventMapper.get_event(message)
                if not isinstance(event, MouseEvent):
                    continue

                # Execute mouse actions in executor to avoid blocking
                if event.action == MouseEvent.MOVE_ACTION:
                    self._move_cursor(event.x, event.y, event.dx, event.dy)
                    # Check for edge crossing after movement
                    await self._check_edge()
                elif event.action == MouseEvent.POSITION_ACTION:
                    for _ in range(
                        10
                    ):  # We position multiple times to ensure it works across platforms
                        await self._position_cursor(event.x, event.y)
                    # Check for edge crossing after positioning
                    await self._check_edge()
                elif event.action == MouseEvent.CLICK_ACTION:
                    # Click is fast enough to run directly
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
        """
        Async event handler for when client becomes active.
        """
        if data is not None:
            self._current_screen = data.client_screen
            # Track which of our monitors the server's spatial routing
            # targeted. Used by ``_position_cursor`` / ``_move_cursor``
            # to pin the cursor to that physical screen instead of the
            # full virtual-desktop bbox.
            self._active_monitor_id = data.client_monitor_id
        # Reset movement history
        self._movement_history.clear()

        self._is_active = True
        self._cross_screen_event.clear()

        # Auto-start if not running
        if not self._running:
            await self.start()

    async def _on_client_inactive(self, data: Optional[ClientActiveEvent]):
        """
        Async event handler for when a client becomes inactive.
        """
        # Reset movement history
        self._movement_history.clear()
        self._cross_screen_event.clear()
        self._is_active = False
        # Drop the target-monitor pin so a subsequent activation that
        # doesn't carry an id (legacy server / hotkey path) doesn't
        # inherit a stale pin.
        self._active_monitor_id = None

    async def _on_client_topology_updated(
        self, data: Optional[ClientTopologyUpdatedEvent]
    ):
        """Cache the topology pushed by the server. ``_check_edge``
        uses it to resolve return-to-server crossings spatially.
        """
        if data is None:
            return
        self._reverse_bindings = list(data.reverse_bindings or [])
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

    def _resolve_return_to_server(
        self,
        edge: ScreenEdge,
        x: float,
        y: float,
    ) -> Optional[tuple[float, float]]:
        """Spatial lookup mirror of ``ServerMouseListener._resolve_cross_screen_target``.

        Given a client-side edge crossing at ``(x, y)``, find the matching
        :class:`ReverseEdgeBinding` and translate it to the server-bbox
        normalised position the server's cursor must land on. Returns
        ``None`` when no spatial topology applies (caller should fall
        back to the legacy logic).
        """
        if not self._reverse_bindings or not self._monitor_layout.monitors:
            return None
        if self._server_bbox is None:
            return None

        # Identify which of our monitors the cursor sits on (mirror of
        # the server's ``find_monitor_at`` + nearest-fallback).
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
        if monitor is None:
            return None

        # Map the cursor's secondary coord to a [0, 1] axis_norm along
        # the matched monitor edge.
        m_w = max(1, monitor.max_x - monitor.min_x)
        m_h = max(1, monitor.max_y - monitor.min_y)
        if edge == ScreenEdge.LEFT or edge == ScreenEdge.RIGHT:
            axis_norm = (y - monitor.min_y) / m_h
        else:
            axis_norm = (x - monitor.min_x) / m_w
        axis_norm = max(0.0, min(1.0, axis_norm))

        edge_str = self._EDGE_TO_STRING_CLIENT.get(edge)
        if edge_str is None:
            return None

        for b in self._reverse_bindings:
            if b.get("client_monitor_id") != monitor.monitor_id:
                continue
            if b.get("client_edge") != edge_str:
                continue
            c_start = float(b.get("client_axis_start", 0.0))
            c_end = float(b.get("client_axis_end", 0.0))
            if c_end <= c_start or not (c_start <= axis_norm < c_end):
                continue

            # Linear map: position along client edge → position along
            # the matching server-edge slot.
            local_norm = (axis_norm - c_start) / (c_end - c_start)
            s_start = float(b.get("server_axis_start", 0.0))
            s_end = float(b.get("server_axis_end", 0.0))
            server_axis = s_start + local_norm * (s_end - s_start)

            s_min_x = int(b.get("server_monitor_min_x", 0))
            s_min_y = int(b.get("server_monitor_min_y", 0))
            s_max_x = int(b.get("server_monitor_max_x", 0))
            s_max_y = int(b.get("server_monitor_max_y", 0))
            s_edge = b.get("server_edge")
            # Cursor must land just inside the destination edge so the
            # server's edge detector doesn't immediately re-trigger.
            if s_edge == "right":
                target_x = s_max_x - 1
                target_y = s_min_y + server_axis * max(1, s_max_y - s_min_y)
            elif s_edge == "left":
                target_x = s_min_x
                target_y = s_min_y + server_axis * max(1, s_max_y - s_min_y)
            elif s_edge == "bottom":
                target_x = s_min_x + server_axis * max(1, s_max_x - s_min_x)
                target_y = s_max_y - 1
            elif s_edge == "top":
                target_x = s_min_x + server_axis * max(1, s_max_x - s_min_x)
                target_y = s_min_y
            else:
                continue

            bx0, by0, bx1, by1 = self._server_bbox
            bw = max(1, bx1 - bx0)
            bh = max(1, by1 - by0)
            x_norm = (target_x - bx0) / bw
            y_norm = (target_y - by0) / bh
            return (
                max(0.0, min(1.0, x_norm)),
                max(0.0, min(1.0, y_norm)),
            )

        return None

    # Mirror lookup table to avoid per-call dict allocation (used by
    # ``_resolve_return_to_server`` on the worker hot path).
    _EDGE_TO_STRING_CLIENT: dict = {
        ScreenEdge.LEFT: "left",
        ScreenEdge.RIGHT: "right",
        ScreenEdge.TOP: "top",
        ScreenEdge.BOTTOM: "bottom",
    }

    async def _mouse_event_callback(self, message):
        """
        Async callback function to handle mouse events received from the stream.
        """
        try:
            # Auto-start if not running
            if not self._running:
                await self.start()

            # Ignore events if crossing screen or inactive
            if self._cross_screen_event.is_set() or not self._is_active:
                return await asyncio.sleep(0)

            # Put message in async queue
            await self._queue.put(message)
            return None
        except Exception as e:
            self._logger.log(f"Failed to process mouse event ({e})", Logger.ERROR)
            return None

    async def _check_edge(self):
        """
        Check if the mouse cursor is at the edge of the screen and handle accordingly.
        This is called after cursor movement. Optimized for maximum speed.
        """
        if (
            self._checking_edge
            or self._cross_screen_event.is_set()
            or not self._is_active
        ):
            return await asyncio.sleep(0)

        try:
            # Acquisisci il lock per serializzare i controlli edge
            async with self._edge_check_lock:
                # Double-check dopo aver acquisito il lock
                if self._cross_screen_event.is_set() or not self._is_active:
                    return await asyncio.sleep(0)

                self._checking_edge = True

                # Get the current cursor position
                pos = self._controller.position
                if pos is None or len(pos) != 2:  # Invalid position
                    return None
                x, y = pos

                # Add the current position to the movement history
                self._movement_history.append((x, y))

                # Need at least 2 positions to determine direction
                if len(self._movement_history) < self.MOVEMENT_HISTORY_N_THRESHOLD:
                    return None

                edge = EdgeDetector.is_at_edge(
                    movement_history=self._movement_history,
                    x=x,
                    y=y,
                    screen_size=self._monitor_layout,
                    is_dragging=False,  # Force to false, we will handle it separately
                )

                if edge:
                    # Clamp cursor position to the virtual-desktop bounds
                    # so a cursor that overshoots past a secondary-monitor
                    # edge snaps back into a valid pixel.
                    cx, cy = EdgeDetector.clamp_to_screen(x, y, self._screen_bbox)
                    if (cx, cy) != (x, y):
                        try:
                            self._controller.position = (cx, cy)
                            x, y = cx, cy
                        except Exception as e:
                            self._logger.log(
                                f"Failed to clamp cursor to screen ({e})", Logger.ERROR
                            )

                # If we reach an edge, dispatch event to deactivate client and send cross screen message to server
                if (
                    edge and not self._is_dragging
                ):  # Don't trigger edge crossing if dragging
                    # Spatial lookup first: if the server pushed a
                    # topology, use it. The reverse bindings cover
                    # exactly the abutments that exist between this
                    # client's monitors and the server's monitors —
                    # any other edge crossing is into empty space and
                    # must NOT route back to the server.
                    resolved = self._resolve_return_to_server(edge, x, y)
                    if resolved is not None:
                        target_x, target_y = resolved
                    elif self._reverse_bindings:
                        # Topology is present but no binding matched
                        # this edge / cursor position — there's no
                        # adjacency here, so stay on the client.
                        return None
                    else:
                        # No topology pushed yet (legacy server, hotkey
                        # path, or pairing without layout): fall back to
                        # the ScreenPosition-based mapping.
                        target_x, target_y = EdgeDetector.get_crossing_coords(
                            x=x,
                            y=y,
                            screen_size=self._screen_bbox,
                            edge=edge,
                            screen=self._current_screen,
                        )
                        if target_x == -1 and target_y == -1:
                            return None

                    # Set event BEFORE clearing history to block concurrent checks
                    self._cross_screen_event.set()

                    # Clear movement history atomically
                    self._movement_history.clear()

                    command = CrossScreenCommandEvent(x=target_x, y=target_y)

                    # Send command and dispatch event sequentially
                    await self.command_stream.send(command)
                    await self.event_bus.dispatch(
                        event_type=BusEventType.CLIENT_INACTIVE, data=None
                    )

                    return await asyncio.sleep(0)

        except Exception as e:
            self._logger.log(f"Failed to dispatch screen event ({e})", Logger.ERROR)
        finally:
            self._checking_edge = False

        return await asyncio.sleep(0)

    def _target_bbox(self) -> tuple[int, int, int, int]:
        """Return the bbox that incoming normalized ``(x, y)`` coords
        should be denormalized against.

        When the server's spatial routing has pinned a specific monitor
        on this client (via the most recent ``ClientActiveEvent``), use
        that monitor's bounds — otherwise fall back to the full virtual
        desktop bbox (legacy single-monitor behaviour).
        """
        if self._active_monitor_id is not None and self._monitor_layout.monitors:
            for m in self._monitor_layout.monitors:
                if m.monitor_id == self._active_monitor_id:
                    return (m.min_x, m.min_y, m.max_x, m.max_y)
        return self._screen_bbox

    async def _position_cursor(self, x: float | int, y: float | int):
        """
        Position the mouse cursor to the specified (x, y) coordinates.

        Denormalizes against the active target monitor when the server
        signalled one; otherwise against the full virtual-desktop bbox.
        """
        try:
            min_x, min_y, max_x, max_y = self._target_bbox()
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
            # On some platforms, positioning may fail when cursor misses
            self._logger.log(f"Failed to position cursor ({e})", Logger.ERROR)

    def _move_cursor(
        self, x: float | int, y: float | int, dx: float | int, dy: float | int
    ):
        """
        Move the mouse cursor to the specified (x, y) coordinates.
        """
        # if dx and dy are provided, use relative movement
        if x == -1 and y == -1:
            # Convert to int for pynput
            try:
                dx = int(dx)
                dy = int(dy)
            except ValueError:
                dx = 0
                dy = 0

            self._controller.move(dx=dx, dy=dy)
        else:
            try:
                # Denormalize against the active target monitor (set by
                # the server's spatial routing) or the full virtual bbox
                # when no target is pinned.
                min_x, min_y, max_x, max_y = self._target_bbox()
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
                # On some platforms, positioning may fail when cursor misses
                self._logger.log(f"Failed to position cursor ({e})", Logger.ERROR)

    def _click(self, button: int | None, is_pressed: bool):
        """
        Perform a mouse click action.

        Each press/release is forwarded 1:1 to the OS so no click is dropped.
        Consecutive presses on the same button within DOUBLE_CLICK_THRESHOLD are
        tagged with an incrementing click_count (1, 2, 3, ...) which lets the OS
        recognise double/triple clicks.
        """
        try:
            name = ButtonMapping(button).name
            btn = Button[name]
        except (ValueError, KeyError):
            return

        if is_pressed:
            # Defensive: if we're already pressed (e.g. duplicated or
            # reordered press event), release first so we don't get stuck.
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
        """
        Hint the next press/release with the desired click_count.

        Only meaningful on macOS where pynput.Controller increments its
        ``_click`` attribute inside ``_press`` and tags the Quartz event with
        ``kCGMouseEventClickState``.
        """
        if not hasattr(self._controller, "_click"):
            return
        try:
            # pynput's macOS _press does `self._click += 1` before posting,
            # so set count-1 to land on the desired value.
            self._controller._click = max(count - 1, 0)
        except Exception:
            pass

    def _scroll(self, dx: int | float, dy: int | float):
        """
        Perform a mouse scroll action.
        """
        try:
            dx = int(dx)
            dy = int(dy)
        except ValueError:
            return

        self._controller.scroll(dx, dy)
