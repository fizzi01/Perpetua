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
    
    # Offset from screen edge (normalized)
    _EDGE_OFFSET = 0.02
    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5

    def __init__(self, *args, **kwargs):
        self._barrier_mode = is_wayland() and (is_gnome() or is_kde())
        super().__init__(*args, **kwargs)

        if self._barrier_mode:
            self._active_client_barrier: Optional[str] = None
            self._barrier_screen_size: tuple[int, int] = Screen.get_size()

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
            self._listener.update_clients(dict(self._active_screens))

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
            self._logger.debug(f"[GUARD] hotkey activation screen={active_screen}")
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
                f"[GUARD] RELEASE client={self._active_client_barrier} x={x} y={y}"
            )
            if self._listener:
                self._listener.disable_capture(x, y)
            self._active_client_barrier = None
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=data,
            )

        await asyncio.sleep(0)

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
                    is_presed=pressed,
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
        self._logger.debug(f"[BARRIER_HIT] edge={edge} cx={cx} cy={cy}")
        asyncio.run_coroutine_threadsafe(
            self._on_barrier_activated(edge, cx, cy),
            self._loop,
        )

    async def _on_barrier_activated(self, edge: str, cursor_x: float, cursor_y: float):
        """Dispatch cross-screen events when a barrier is hit."""
        screen = edge

        if not screen or not self._active_screens.get(screen, False):
            return

        if self._active_client_barrier is not None:
            return

        self._active_client_barrier = screen

        off = self._EDGE_OFFSET
        sw, sh = self._barrier_screen_size
        mouse_event = MouseEvent(x=0, y=0, action=MouseEvent.POSITION_ACTION)

        if edge == "left":
            mouse_event.x = 1.0 - off
            mouse_event.y = cursor_y / sh if sh else 0.5
        elif edge == "right":
            mouse_event.x = off
            mouse_event.y = cursor_y / sh if sh else 0.5
        elif edge == "top":
            mouse_event.x = cursor_x / sw if sw else 0.5
            mouse_event.y = 1.0 - off
        elif edge == "bottom":
            mouse_event.x = cursor_x / sw if sw else 0.5
            mouse_event.y = off

        self._logger.debug(
            f"[BARRIER_ACT] SENDING position x={mouse_event.x:.4f} y={mouse_event.y:.4f} "
            f"to client={screen}"
        )

        try:
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=ActiveScreenChangedEvent(active_screen=screen),
            )
            await self.command_stream.send(CrossScreenCommandEvent(target=screen))
            await self.stream.send(mouse_event)
        except Exception as e:
            self._logger.error(f"Error dispatching cross-screen event: {e}")
            self._active_client_barrier = None


class ServerMouseController(_base.ServerMouseController):
    """Linux server-side mouse controller."""

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
                    self.position_cursor(x, y)


class ClientMouseController(_base.ClientMouseController):
    """Linux client-side mouse controller."""

    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5

    pass
