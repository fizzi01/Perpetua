"""
Provides mouse input support for Windows systems.
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
import ctypes
from typing import Optional

from event import (
    BusEventType,
    MouseEvent,
    ActiveScreenChangedEvent,
    ClientDisconnectedEvent,
)

from . import _base


class ServerMouseListener(_base.ServerMouseListener):
    """Windows server mouse listener with in-process capture / cursor
    management.

    Why this replaces the wx-based cursor handler subprocess on Windows
    (see ``src/input/cursor/_win.py`` for the matching noop worker):

    - pynput's ``MouseListener`` is already a ``SetWindowsHookEx(WH_MOUSE_LL)``
      hook that delivers every cursor motion to ``on_move`` independently
      of focus / capture / z-order. Forwarding deltas to the client from
      here is one branch on the existing hot path; spinning up a wx
      overlay subprocess to do the same thing was 50-150 ms of
      ``Show + Raise + SetFocus + CaptureMouse + WarpPointer`` per
      cross-screen and the root cause of the focus-drop / capture-lost
      bugs investigated earlier in the multi-monitor refactor.
    - Cursor visibility is handled via ``ShowCursor`` (refcounted
      system-wide). No window, no focus battle.
    - The cursor is re-centred on the primary display after every
      forwarded delta so the OS never clamps it against a screen edge —
      a clamped cursor stops generating ``WM_MOUSEMOVE`` and starves
      the listener of motion events.

    macOS keeps the original wx-based overlay (``CGDisplayHideCursor``
    has different semantics that integrate cleanly with the overlay);
    only Windows takes this fast path.
    """

    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Listening-mode anchor: the cursor position observed at the
        # last ``on_move`` tick (post-recentre), used as the baseline
        # for the next delta. ``None`` until the first sample after
        # ``_listening`` flips to ``True``.
        self._listening_last_pos: Optional[tuple[int, int]] = None
        # Silence the synthetic ``on_move`` triggered by our own
        # ``MouseController.position = centre`` recentre call, so we
        # don't ship a delta equal-and-opposite to the user's real
        # motion. Also primed when listening starts to swallow the
        # cursor warp emitted by ``enable_capture``.
        self._listening_recenter_pending: bool = False
        # Recentre target — centre of the primary server monitor.
        # Refreshed on every listening transition so it tracks any
        # monitor-layout change between sessions.
        self._listening_center: tuple[int, int] = (0, 0)
        # Cursor visibility refcount: ``True`` while we owe the system
        # a ``ShowCursor(True)`` to balance our ``ShowCursor(False)``
        # during a listening session. Used to avoid double hide / show.
        self._cursor_hidden: bool = False
        # ``user32`` handle for ``ShowCursor`` / ``SetCursorPos`` /
        # ``GetSystemMetrics``. Cached so the hot path doesn't re-import
        # ctypes on every event.
        self._user32 = ctypes.windll.user32

        # Receive the cross-screen activation request directly: there
        # is no cursor handler subprocess on Windows, so this listener
        # owns the entire enable/disable-capture flow.
        self.event_bus.subscribe(
            event_type=BusEventType.SCREEN_CHANGE_GUARD,
            callback=self._on_screen_change_guard,
            priority=True,
        )
        # Defensive: re-show the cursor if the active client drops out
        # without an explicit return-to-server, otherwise the user's
        # cursor stays invisible.
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected_show_cursor,
            priority=True,
        )

    # ------------------------------------------------------------------
    # SCREEN_CHANGE_GUARD: in-process replacement for what the wx-based
    # CursorHandlerWorker does on macOS. Same ordering contract:
    # - going TO client: dispatch ACTIVE_SCREEN_CHANGED FIRST (so the
    #   server-side controller positions / blanks the cursor), THEN
    #   hide + recentre;
    # - returning FROM client: show cursor FIRST, THEN dispatch (so the
    #   subsequent ``position_cursor`` warp lands on a visible cursor).
    # ------------------------------------------------------------------

    async def _on_screen_change_guard(
        self, data: Optional[ActiveScreenChangedEvent]
    ) -> None:
        if data is None:
            return
        active_screen = data.active_screen
        if active_screen:
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=data,
            )
            await asyncio.sleep(0)
            self._enable_capture()
        else:
            self._disable_capture()
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=data,
            )

    async def _on_client_disconnected_show_cursor(
        self, data: Optional[ClientDisconnectedEvent]
    ) -> None:
        # Re-show the cursor if the listening session is implicitly
        # torn down by a disconnect rather than a clean return-to-server.
        if self._cursor_hidden:
            self._disable_capture()

    def _enable_capture(self) -> None:
        """Hide the cursor system-wide and warp it to the primary
        monitor centre so the listener delta loop has a known anchor.

        ``ShowCursor`` is refcounted: each ``False`` call decrements
        an internal counter, each ``True`` increments. The cursor is
        invisible when the counter is below zero; we loop until that's
        the case in case another app pushed the counter above zero.
        """
        if self._cursor_hidden:
            return
        try:
            while self._user32.ShowCursor(False) >= 0:
                pass
            self._cursor_hidden = True
        except Exception as e:
            self._logger.error(f"ShowCursor(False) failed ({e})")
        # Prime the listening-mode state. The first ``on_move`` we
        # receive after this point is either the synthetic warp below
        # or — if the user is still moving — a real motion; the
        # ``_listening_recenter_pending`` flag silences it either way,
        # so the second sample becomes the baseline.
        self._listening_last_pos = None
        self._listening_recenter_pending = True
        self._listening_center = self._compute_listening_center()
        try:
            self._user32.SetCursorPos(
                self._listening_center[0], self._listening_center[1]
            )
        except Exception as e:
            self._logger.error(f"SetCursorPos centre failed ({e})")

    def _disable_capture(self) -> None:
        """Restore cursor visibility. The actual cursor position on
        return-to-server is set by :class:`ServerMouseController` via
        the ACTIVE_SCREEN_CHANGED dispatch that follows this call.
        """
        if not self._cursor_hidden:
            return
        try:
            while self._user32.ShowCursor(True) < 0:
                pass
        except Exception as e:
            self._logger.error(f"ShowCursor(True) failed ({e})")
        self._cursor_hidden = False
        self._listening_last_pos = None
        self._listening_recenter_pending = False

    def _compute_listening_center(self) -> tuple[int, int]:
        """Primary-monitor centre, with fallbacks.

        Prefers the monitor layout (which knows about multi-monitor
        server arrangements); falls back to the cached screen size if
        the layout is empty.
        """
        if self._monitor_layout.monitors:
            primary = next(
                (m for m in self._monitor_layout.monitors if m.is_primary),
                self._monitor_layout.monitors[0],
            )
            return (
                (primary.min_x + primary.max_x) // 2,
                (primary.min_y + primary.max_y) // 2,
            )
        return (self._screen_size[0] // 2, self._screen_size[1] // 2)

    # ------------------------------------------------------------------
    # on_move: forwards deltas while listening, falls back to the base
    # buffering / edge-detection path otherwise.
    # ------------------------------------------------------------------

    def on_move(self, x, y):  # noqa: D401 - matches pynput signature
        if not self._screen_size_valid():
            return True
        if self._listening:
            return self._on_move_listening(x, y)
        return super().on_move(x, y)

    def _on_move_listening(self, x, y) -> bool:
        # Silence the synthetic motion produced by our own recentre.
        if self._listening_recenter_pending:
            self._listening_recenter_pending = False
            self._listening_last_pos = (x, y)
            return True
        # First sample after entering listening mode — establish the
        # baseline, no delta to ship yet.
        if self._listening_last_pos is None:
            self._listening_last_pos = (x, y)
            return True

        dx = x - self._listening_last_pos[0]
        dy = y - self._listening_last_pos[1]
        if dx or dy:
            mouse_event = MouseEvent(dx=dx, dy=dy, action=MouseEvent.MOVE_ACTION)
            try:
                self._schedule_async(self.stream.send(mouse_event))
            except Exception as e:
                self._logger.error(f"Failed to forward listening delta ({e})")

        # Recentre. Keeps the cursor well clear of every screen edge so
        # the OS never clamps it (a clamped cursor stops generating
        # ``WM_MOUSEMOVE`` and starves the listener of motion events).
        cx, cy = self._listening_center
        if (x, y) != (cx, cy):
            self._listening_recenter_pending = True
            try:
                self._user32.SetCursorPos(cx, cy)
                self._listening_last_pos = (cx, cy)
            except Exception as e:
                self._logger.error(f"Failed to recentre cursor ({e})")
                self._listening_last_pos = (x, y)
        else:
            self._listening_last_pos = (x, y)
        return True

    # ------------------------------------------------------------------
    # Native suppression filter retained for backward compatibility
    # (only takes effect when ``filtering=True`` is passed in __init__,
    # which the daemon doesn't do today).
    # ------------------------------------------------------------------

    def _win32_mouse_suppress_filter(self, msg, data):
        """
        Suppress mouse events when listening.
        """
        if self._listening:
            # msg = 513/514 -> left down/up
            # msg = 516/517 -> right down/up
            # msg = 519/520 -> middle down/up
            # msg = 522/523 -> scroll
            if msg in (513, 514, 516, 517, 519, 520, 522, 523):
                self._listener._suppress = True  # ty: ignore
                return False
            else:
                self._listener._suppress = False  # ty: ignore
        else:
            self._listener._suppress = False  # ty: ignore

        return True


class ServerMouseController(_base.ServerMouseController):
    """
    It controls mouse events on Windows systems.
    Its main purpose is to move the mouse cursor and perform clicks.
    """

    pass


class ClientMouseController(_base.ClientMouseController):
    """
    It controls mouse events on Windows systems.
    Its main purpose is to move the mouse cursor and perform clicks.
    """

    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5

    pass
