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
from collections import deque

from typing import Optional

from input._platform import is_wayland, is_gnome, is_kde
from . import _base

from event import (
    BusEventType,
    MouseEvent,
    ActiveScreenChangedEvent,
    CrossScreenCommandEvent,
    ClientConnectedEvent,
    ClientDisconnectedEvent,
)
from utils.screen import Screen


class ServerMouseListener(_base.ServerMouseListener):
    """Linux mouse listener (Wayland barrier mode or X11 pynput)."""

    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5

    def __init__(self, *args, **kwargs):
        self._barrier_mode = is_wayland() and (is_gnome() or is_kde())
        super().__init__(*args, **kwargs)

        if self._barrier_mode:
            self._active_client_barrier: Optional[str] = None
            self._barrier_screen_size: tuple[int, int] = Screen.get_size()
            self._pending_events: deque = deque(maxlen=10000)
            self._drain_task: Optional[asyncio.Task] = None

            self.event_bus.subscribe(
                event_type=BusEventType.SCREEN_CHANGE_GUARD,
                callback=self._on_screen_change_guard_wayland,
            )

    def _create_listener(self):
        if self._barrier_mode:
            from .backend import MouseListener

            return MouseListener(
                on_move=self._on_barrier_move,
                on_click=self._on_barrier_click,
                on_scroll=self._on_barrier_scroll,
                on_barrier=self._on_barrier_hit,
            )
        return super()._create_listener()

    # -- start / stop / is_alive -------------------------------------------

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
        self._listener.update_clients(dict(self._active_screens))

        self._drain_task = self._loop.create_task(self._event_drain())

        self._logger.debug("Wayland barrier mode started")
        return True

    def stop(self) -> bool:
        if self._barrier_mode:
            return self._stop_barrier()
        return super().stop()

    def _stop_barrier(self) -> bool:
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
        if self._listener:
            self._listener.stop()
        self._logger.debug("Wayland barrier mode stopped")
        return True

    def is_alive(self):
        if self._barrier_mode:
            return self._listener.is_alive() if self._listener else False
        return super().is_alive()

    # -- Client tracking -> update backend barriers ------------------------

    async def _on_client_connected(self, data: Optional[ClientConnectedEvent]):
        await super()._on_client_connected(data)
        if self._barrier_mode and data is not None and self._listener:
            self._listener.update_clients(dict(self._active_screens))

            if self._drain_task is None or self._drain_task.done():
                if self._loop:
                    self._drain_task = self._loop.create_task(self._event_drain())

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        if self._barrier_mode and data is not None:
            client = data.client_screen
            if self._active_client_barrier and client == self._active_client_barrier:
                self._active_client_barrier = None
                if self._listener:
                    self._listener.disable_capture()
                await self.event_bus.dispatch(
                    event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                    data=ActiveScreenChangedEvent(active_screen=None),
                )

        await super()._on_client_disconnected(data)

        if self._barrier_mode and data is not None and self._listener:
            self._listener.update_clients(dict(self._active_screens))

    # -- SCREEN_CHANGE_GUARD handler ---------------------------------------

    async def _on_screen_change_guard_wayland(self, data):
        """Handle SCREEN_CHANGE_GUARD on Wayland."""
        if data is None:
            return

        active_screen = data.active_screen

        if active_screen:
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=data,
            )
            self._active_client_barrier = active_screen
        else:
            x = getattr(data, "x", -1)
            y = getattr(data, "y", -1)
            if x is None:
                x = -1
            if y is None:
                y = -1
            if self._listener:
                self._listener.disable_capture(x, y)
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=data,
            )
            self._active_client_barrier = None

        await asyncio.sleep(0)

    def _on_barrier_move(self, dx, dy):
        self._pending_events.append(("move", dx, dy))

    def _on_barrier_click(self, button, pressed):
        self._pending_events.append(("click", button, pressed))

    def _on_barrier_scroll(self, dx, dy):
        self._pending_events.append(("scroll", dx, dy))

    def _on_barrier_hit(self, edge, cx, cy):
        self._pending_events.append(("barrier", edge, cx, cy))

    async def _event_drain(self):
        """Read pending events from the deque and forward to streams."""
        move_event = MouseEvent(action=MouseEvent.MOVE_ACTION)

        while self._listener and self._listener.is_alive():
            if not self._pending_events:
                await asyncio.sleep(0.001)
                continue

            try:
                kind, *args = self._pending_events.popleft()

                if kind == "move":
                    move_event.dx = args[0]
                    move_event.dy = args[1]
                    await self.stream.send(move_event)

                elif kind == "click":
                    btn_event = MouseEvent(
                        button=args[0],
                        action=MouseEvent.CLICK_ACTION,
                        is_presed=args[1],
                    )
                    await self.stream.send(btn_event)

                elif kind == "scroll":
                    scroll_event = MouseEvent(
                        dx=args[0],
                        dy=args[1],
                        action=MouseEvent.SCROLL_ACTION,
                    )
                    await self.stream.send(scroll_event)

                elif kind == "barrier":
                    await self._on_barrier_activated(args[0], args[1], args[2])

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.exception(f"Event drain error: {e}")
                await asyncio.sleep(0.01)

    async def _on_barrier_activated(self, edge: str, cursor_x: float, cursor_y: float):
        """Dispatch cross-screen events when a barrier is hit."""
        screen = edge

        if not screen or not self._active_screens.get(screen, False):
            return

        self._logger.debug(f"Barrier activated: edge={edge} -> screen={screen}")

        sw, sh = self._barrier_screen_size
        mouse_event = MouseEvent(x=0, y=0, action=MouseEvent.POSITION_ACTION)

        if edge == "left":
            mouse_event.x = 1.0
            mouse_event.y = cursor_y / sh if sh else 0.5
        elif edge == "right":
            mouse_event.x = 0.0
            mouse_event.y = cursor_y / sh if sh else 0.5
        elif edge == "top":
            mouse_event.x = cursor_x / sw if sw else 0.5
            mouse_event.y = 1.0
        elif edge == "bottom":
            mouse_event.x = cursor_x / sw if sw else 0.5
            mouse_event.y = 0.0

        try:
            await self.event_bus.dispatch(
                event_type=BusEventType.SCREEN_CHANGE_GUARD,
                data=ActiveScreenChangedEvent(active_screen=screen),
            )
            await self.command_stream.send(CrossScreenCommandEvent(target=screen))
            await self.stream.send(mouse_event)
        except Exception as e:
            self._logger.error(f"Error dispatching cross-screen event: {e}")


class ServerMouseController(_base.ServerMouseController):
    """Linux server-side mouse controller."""

    pass


class ClientMouseController(_base.ClientMouseController):
    """Linux client-side mouse controller."""

    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5

    pass
