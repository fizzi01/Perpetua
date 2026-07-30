"""Linux mouse input.

On Wayland (GNOME/KDE) barriers are handled by the InputCapture
portal backend; on X11 the base implementation is used.
"""


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

from typing import Optional

from input._platform import is_wayland, is_gnome, is_kde
from input.utils import ScreenEdge
from . import _base

from event import (
    BusEventType,
    MouseEvent,
    ActiveScreenChangedEvent,
    ClientConnectedEvent,
    ClientDisconnectedEvent,
)
from utils.logging import Logger


class ServerMouseListener(_base.ServerMouseListener):
    """Linux mouse listener (Wayland barrier mode or X11 pynput)."""

    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5

    # Barrier edge names as reported by the InputCapture portal, mapped to
    # the shared ``ScreenEdge`` enum so the Wayland path can reuse the
    # base spatial resolver.
    _STRING_TO_SCREEN_EDGE: dict[str, ScreenEdge] = {
        "left": ScreenEdge.LEFT,
        "right": ScreenEdge.RIGHT,
        "top": ScreenEdge.TOP,
        "bottom": ScreenEdge.BOTTOM,
    }

    def __init__(self, *args, **kwargs):
        self._barrier_mode = is_wayland() and (is_gnome() or is_kde())
        super().__init__(*args, **kwargs)

        if self._barrier_mode:
            # UID of the captured client; None while the server owns the cursor.
            self._active_client_barrier: Optional[str] = None
            # Last activation already logged as unroutable (log dedupe only).
            self._last_rejected_activation: int = 0

            self.event_bus.subscribe(
                event_type=BusEventType.SCREEN_CHANGE_GUARD,
                callback=self._on_screen_change_guard_wayland,
            )

    def _barrier_segments(self) -> list[tuple[str, int, int, int, int]]:
        """Absolute barrier segments covering exactly the bound edge portions.

        A binding does not necessarily span a whole server edge - a client
        monitor may sit against only part of it (``server_axis_start/end``).
        A barrier is a *line segment*, so it can say precisely that, and it
        must: an armed barrier holds the pointer, so covering the unbound
        remainder of an edge would stop the cursor short of the real border
        exactly where there is nothing to cross to.

        Returned in absolute desktop coordinates, ready for
        ``portal.set_barriers``, as ``(edge, x1, y1, x2, y2)``.
        """
        by_id = {m.monitor_id: m for m in self._monitor_layout.monitors}
        segments: list[tuple[str, int, int, int, int]] = []

        for bindings in self._edge_bindings_by_client.values():
            for binding in bindings:
                edge = str(binding.get("server_edge") or "")
                monitor = by_id.get(binding.get("server_monitor_id"))
                if edge not in self._STRING_TO_SCREEN_EDGE or monitor is None:
                    continue

                start = max(0.0, min(1.0, float(binding.get("server_axis_start", 0.0))))
                end = max(0.0, min(1.0, float(binding.get("server_axis_end", 1.0))))
                if end <= start:
                    continue

                # A vertical edge is partitioned along y, a horizontal one
                # along x - the same axis convention as the bindings. The end
                # is inclusive, hence ``- 1`` on a half-open range.
                #
                # Every coordinate is coerced with ``int()``: the extension takes
                # ``Vec<(String, i32, i32, i32, i32)>``, and a float arriving from
                # the layout math (a monitor bound read off a scaled display) is
                # rejected by PyO3 as a ``TypeError`` - which is
                # indistinguishable from "this build has no segments keyword" and
                # used to be reported as a missing rebuild.
                if edge in ("left", "right"):
                    span = monitor.max_y - monitor.min_y
                    lo = int(round(monitor.min_y + start * span))
                    hi = max(lo, int(round(monitor.min_y + end * span)) - 1)
                    x = int(monitor.min_x if edge == "left" else monitor.max_x)
                    segments.append((edge, x, int(lo), x, int(hi)))
                else:
                    span = monitor.max_x - monitor.min_x
                    lo = int(round(monitor.min_x + start * span))
                    hi = max(lo, int(round(monitor.min_x + end * span)) - 1)
                    y = int(monitor.min_y if edge == "top" else monitor.max_y)
                    segments.append((edge, int(lo), y, int(hi), y))

        return segments

    def _refresh_edge_state(self) -> dict:
        """Barrier state for the backend: which edges, and which parts of them.

        ``edges`` keeps the coarse per-edge view, used when the installed
        pyinputcapture predates segment support; ``segments`` is the precise
        one and is what should normally take effect.
        """
        segments = self._barrier_segments()
        return {
            "edges": {edge: True for edge in sorted({s[0] for s in segments})},
            "segments": segments,
        }

    def _create_listener(self):
        if self._barrier_mode:
            from .backend import MouseListener

            return MouseListener(
                on_move=self._on_barrier_move,
                on_click=self._on_barrier_click,
                on_scroll=self._on_barrier_scroll,
                on_barrier=self._on_barrier_hit,
                on_state=self._on_barrier_state,
            )
        return super()._create_listener()

    def start(self) -> bool:
        if self._barrier_mode:
            return self._start_barrier()
        return super().start()

    def _start_barrier(self) -> bool:
        if self._listener and self._listener.is_alive():
            return True

        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._logger.warning("No event loop for Wayland barrier mode")
                return False

        if self._listener is None:
            self._listener = self._create_listener()

        self._listener.start()
        self._listener.update_clients(self._refresh_edge_state())

        self._logger.debug("Wayland barrier mode started")
        return True

    def stop(self) -> bool:
        if self._barrier_mode:
            return self._stop_barrier()
        return super().stop()

    def _stop_barrier(self) -> bool:
        if self._listener:
            self._listener.stop()
        self._logger.debug("Wayland barrier mode stopped")
        return True

    def is_alive(self):
        if self._barrier_mode:
            return self._listener.is_alive() if self._listener else False
        return super().is_alive()

    async def _on_client_connected(self, data: Optional[ClientConnectedEvent]):
        await super()._on_client_connected(data)
        if self._barrier_mode and data is not None and self._listener:
            self._listener.update_clients(self._refresh_edge_state())

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        if self._barrier_mode and data is not None:
            client_uid = data.client_uid
            if (
                self._active_client_barrier
                and client_uid == self._active_client_barrier
            ):
                self._active_client_barrier = None
                if self._listener:
                    self._listener.disable_capture()
                await self.event_bus.dispatch(
                    event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                    data=ActiveScreenChangedEvent(active_screen=None),
                )

        await super()._on_client_disconnected(data)

        if self._barrier_mode and data is not None and self._listener:
            self._listener.update_clients(self._refresh_edge_state())

    async def _on_client_layout_updated(self, data):
        # Re-arm the barriers so a newly bound edge (or a resized/moved
        # placement on an already-bound one) takes effect immediately, without
        # waiting for a reconnect. Mirrors the X11 hot-reload.
        await super()._on_client_layout_updated(data)
        if self._barrier_mode and data is not None and self._listener:
            self._listener.update_clients(self._refresh_edge_state())

    async def _on_local_monitors_updated(self, data):
        """Re-arm barriers after a *server* monitor hotplug.

        The segments are computed from ``_monitor_layout``, which the base
        handler has just replaced - stale segments would sit at the old
        monitor's coordinates, arming barriers where no edge is any more and
        leaving the real new edges free.
        """
        await super()._on_local_monitors_updated(data)
        if self._barrier_mode and self._listener:
            self._listener.update_clients(self._refresh_edge_state())

    async def _on_screen_change_guard_wayland(self, data):
        """Handle SCREEN_CHANGE_GUARD on Wayland.

        Barrier activations bypass this handler entirely (they dispatch
        ACTIVE_SCREEN_CHANGED directly).  This only handles:
        - Keyboard hotkey screen switches (active_screen set)
        - Client returning cursor to server (active_screen=None)
        """
        if data is None:
            return

        active_screen = data.active_screen

        if active_screen:
            # Keyboard hotkey activation
            self._logger.debug("[GUARD] hotkey activation", active_screen=active_screen)
            self._active_client_barrier = active_screen
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=data,
            )
        else:
            # Client returning cursor to server
            if self._active_client_barrier is None:
                self._logger.debug("[GUARD] REJECTED release: no active client")
                return

            x = getattr(data, "x", -1)
            y = getattr(data, "y", -1)
            if x is None:
                x = -1
            if y is None:
                y = -1
            self._logger.debug(
                "[GUARD] RELEASE",
                client=self._active_client_barrier,
                x=x,
                y=y,
            )
            if self._listener:
                # This release repositions the cursor onto the server edge, so the
                # recapture it provokes is spurious and must be swallowed.
                self._listener.disable_capture(x, y, suppress_recapture=True)
            self._active_client_barrier = None
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=data,
            )

        await asyncio.sleep(0)

    def _on_barrier_state(self, healthy: bool, reason):
        """Capture-session health, reported from the backend thread.

        Logged at warning level so a session that died and is being retried is
        visible: it used to fail silently, leaving a server that looked healthy
        while no edge could be crossed. ``is_alive()`` reflects the same state,
        which is what lets the service layer restart the listener.
        """
        if healthy:
            self._logger.info("Wayland capture session healthy")
            return
        self._logger.warning("Wayland capture session unavailable", reason=reason)
        # The cursor can't be on a client if capture is gone; drop the local
        # belief so a later activation isn't rejected by the in-flight guard.
        self._active_client_barrier = None

    def _on_barrier_move(self, dx, dy):
        asyncio.run_coroutine_threadsafe(
            self.stream.send(MouseEvent(dx=dx, dy=dy, action=MouseEvent.MOVE_ACTION)),
            self._loop,
        )

    def _on_barrier_click(self, button, pressed):
        asyncio.run_coroutine_threadsafe(
            self.stream.send(
                MouseEvent(
                    button=button,
                    action=MouseEvent.CLICK_ACTION,
                    is_pressed=pressed,
                )
            ),
            self._loop,
        )

    def _on_barrier_scroll(self, dx, dy):
        asyncio.run_coroutine_threadsafe(
            self.stream.send(MouseEvent(dx=dx, dy=dy, action=MouseEvent.SCROLL_ACTION)),
            self._loop,
        )

    def _on_barrier_hit(self, edge, cx, cy):
        if self._logger.is_enabled_for(Logger.DEBUG):
            self._logger.debug("[BARRIER_HIT]", edge=edge, cx=cx, cy=cy)
        asyncio.run_coroutine_threadsafe(
            self._on_barrier_activated(edge, cx, cy),
            self._loop,
        )

    async def _on_barrier_activated(self, edge: str, cursor_x: float, cursor_y: float):
        """Dispatch cross-screen events when a barrier is hit.

        ``cursor_x/cursor_y`` come from the InputCapture portal in absolute
        desktop coordinates, which is exactly what
        ``_resolve_cross_screen_target`` wants - so this path resolves the
        target through the same spatial EdgeBinding lookup as every other
        backend instead of the edge->UID collapse it used to do. That is
        what makes the per-axis partitioning, the ``client_monitor_id`` and
        the ``client_edge`` available here, and going through
        ``_send_activation_packets`` is what gives the client the topology
        it needs to route back to the server at all.
        """
        if self._active_client_barrier is not None:
            return

        screen_edge = self._STRING_TO_SCREEN_EDGE.get(edge)
        if screen_edge is None:
            return

        resolved = self._resolve_cross_screen_target(
            edge=screen_edge,
            cursor_x=cursor_x,
            cursor_y=cursor_y,
        )
        if resolved is None:
            # Nothing is placed at this point on this edge. Do NOT cross: a
            # binding covers only the portion of the edge its client monitor
            # abuts (``server_axis_start/end``), and honouring an activation
            # outside that range would teleport the cursor to a client that
            # isn't there. Segment barriers normally keep us out of this
            # branch entirely; it still fires when the compositor rejected a
            # segment and fell back to a whole-edge barrier.
            # One line per activation, not per tick: a user leaning on an
            # unbound stretch of edge re-triggers this continuously.
            activation_id = (
                self._listener.current_activation_id if self._listener else 0
            )
            if activation_id != self._last_rejected_activation:
                self._last_rejected_activation = activation_id
                self._logger.debug(
                    "[BARRIER_ACT] no binding at activation point; releasing",
                    edge=edge,
                    cx=cursor_x,
                    cy=cursor_y,
                )
            # The compositor is holding the pointer at the barrier right now -
            # returning without releasing would strand it there. No recapture
            # suppression: this release doesn't move the cursor, and swallowing
            # the next activation would eat a legitimate crossing.
            if self._listener:
                self._listener.disable_capture()
            return

        target_uid, binding, server_axis_norm = resolved

        mouse_event = MouseEvent(x=0, y=0, action=MouseEvent.POSITION_ACTION)
        client_monitor_id, client_entry_edge = self._apply_landing_to_event(
            mouse_event, screen_edge, binding, server_axis_norm
        )

        self._active_client_barrier = target_uid

        self._logger.debug(
            "[BARRIER_ACT] SENDING position",
            x=round(mouse_event.x, 4),
            y=round(mouse_event.y, 4),
            client_uid=target_uid,
            edge=edge,
            entry_edge=client_entry_edge,
            monitor=client_monitor_id,
            resolved=binding is not None,
        )

        try:
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=ActiveScreenChangedEvent(active_screen=target_uid),
            )
            await self._send_activation_packets(
                target_uid,
                mouse_event,
                client_monitor_id,
                client_entry_edge,
            )
        except Exception as e:
            self._logger.error("Error dispatching cross-screen event", error=str(e))
            self._active_client_barrier = None


class ServerMouseController(_base.ServerMouseController):
    """Linux server-side mouse controller."""

    def __init__(self, *args, **kwargs):
        self._barrier_mode = is_wayland() and (is_gnome() or is_kde())
        super().__init__(*args, **kwargs)

    def _create_controller(self):
        """No controller in barrier mode - the portal owns the placement.

        Building one would open a RemoteDesktop portal session plus a libei
        dispatch thread that nothing then uses, and leave them running past
        ``Server.stop()``.
        """
        if self._barrier_mode:
            return None
        return super()._create_controller()

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        """
        Activate only when the active screen becomes None.
        """
        if self._barrier_mode:
            # On Wayland the InputCapture portal is the ONLY authority for
            # the return landing: ``portal.release(x, y)`` places the cursor
            # while releasing the capture. Writing it again here would go
            # through the libei RemoteDesktop controller, whose position is a
            # virtual accumulator seeded at (0, 0) that nothing ever syncs to
            # the real pointer on a server (the user's physical mouse moves
            # the cursor without libei's knowledge). That second write
            # therefore commands an arbitrary displacement and is what kept
            # the cursor off the real monitor border after a return.
            return
        if data is not None:
            active_screen = data.active_screen
            if active_screen is None:
                # Get the cursor position from data if available
                x = data.x
                y = data.y
                if x > -1 and y > -1:
                    self.position_cursor(x, y)


class ClientMouseController(_base.ClientMouseController):
    """Linux client-side mouse controller."""

    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5

    async def stop(self):
        await super().stop()
        if not (is_wayland() and (is_gnome() or is_kde())):
            return
        # Close the RemoteDesktop portal session used for injection. It lives
        # in a module-level singleton, so without this it outlives the client
        # service and the next start is handed a connection whose compositor
        # session is already gone.
        try:
            from .backend._libei import shutdown_connection

            shutdown_connection()
        except Exception as exc:
            self._logger.debug("libei connection shutdown failed", error=str(exc))
