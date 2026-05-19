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
import atexit
import ctypes
import sys
from ctypes import wintypes
from typing import Optional

from event import (
    BusEventType,
    MouseEvent,
    ActiveScreenChangedEvent,
    ClientDisconnectedEvent,
)

from . import _base


# ---------------------------------------------------------------------------
# System-cursor hide / restore
#
# ``ShowCursor`` on Windows is per-thread (MSDN: "the cursor's display state
# is per-thread") and only takes effect when the calling thread owns the
# foreground window. The KVM service is rarely in foreground while listening,
# so ``ShowCursor`` is useless for our case.
#
# ``SetSystemCursor`` replaces the user-session system cursors with a custom
# bitmap, taking effect immediately for EVERY app. It's the only cross-thread
# way to make the cursor invisible system-wide without UIAccess privileges.
# The catch: SetSystemCursor destroys the previously-installed cursor handle,
# so we can't manually restore — instead we use
# ``SystemParametersInfoW(SPI_SETCURSORS)`` which the OS uses to reload the
# user's configured cursors from the registry.
#
# An ``atexit`` hook restores the cursors on clean shutdown; a crash leaves
# the user with a blank cursor until the next reboot (or until they run
# the same ``SystemParametersInfo`` call). Acceptable trade-off vs. failing
# to hide the cursor at all.
# ---------------------------------------------------------------------------

_OCR_IDS: tuple[int, ...] = (
    32512,  # OCR_NORMAL
    32513,  # OCR_IBEAM
    32514,  # OCR_WAIT
    32515,  # OCR_CROSS
    32516,  # OCR_UP
    32642,  # OCR_SIZENWSE
    32643,  # OCR_SIZENESW
    32644,  # OCR_SIZEWE
    32645,  # OCR_SIZENS
    32646,  # OCR_SIZEALL
    32648,  # OCR_NO
    32649,  # OCR_HAND
    32650,  # OCR_APPSTARTING
)
_SPI_SETCURSORS = 0x0057


def _restore_system_cursors() -> None:
    """Reload the user's configured system cursors from the registry.
    Cheap and idempotent — safe to call repeatedly and from ``atexit``.
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SystemParametersInfoW(_SPI_SETCURSORS, 0, None, 0)
    except Exception:
        pass


if sys.platform == "win32":
    # Belt-and-suspenders: restore the user's cursors if the process
    # crashes without making it to ``disable_capture``.
    atexit.register(_restore_system_cursors)


def _hide_system_cursors() -> None:
    """Replace every system cursor with a fully transparent bitmap.

    32x32 monochrome cursor: AND mask all-1 + XOR mask all-0 → every
    pixel is transparent (MSDN cursor truth table). Issue once per OCR
    id; ``SetSystemCursor`` takes ownership of each handle.
    """
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    width, height = 32, 32
    mask_size = (width // 8) * height  # 128 bytes for a 32x32 monochrome mask
    and_mask = (ctypes.c_byte * mask_size)(*([0xFF] * mask_size))
    xor_mask = (ctypes.c_byte * mask_size)(*([0x00] * mask_size))
    for ocr_id in _OCR_IDS:
        try:
            blank = user32.CreateCursor(
                None,
                0,
                0,
                width,
                height,
                ctypes.byref(and_mask),
                ctypes.byref(xor_mask),
            )
            if blank:
                user32.SetSystemCursor(blank, ocr_id)
        except Exception:
            # Best-effort: continue with the next OCR id rather than
            # leave the user with a partially-blanked cursor set.
            continue


class ServerMouseListener(_base.ServerMouseListener):
    """Windows server mouse listener with in-process cursor capture.

    Three components, no wxPython overlay subprocess:

    1. **Hide cursor system-wide** (`SetSystemCursor` with a blank
       bitmap on every OCR id). ``ShowCursor`` is per-thread and
       useless cross-app; replacing the system cursors is the only
       reliable way to make the cursor invisible to every app while
       we listen.
    2. **Suppress click / scroll** through pynput's
       ``win32_event_filter``. The base class wires this up only when
       ``filtering=True``; we force it on regardless of how the daemon
       constructs us — a visible-cursor that clicks through to the
       desktop is the worst possible UX.
    3. **Capture deltas by polling ``GetCursorPos``** at 5 ms. The
       ``WH_MOUSE_LL`` hook (pynput's ``on_move``) stops firing when
       the OS clamps the cursor against a screen edge; polling is the
       only way to keep capturing motion intent across that edge.
       Each tick reads the cursor position, ships the delta over the
       stream, and resets the cursor back to the primary-display
       centre — so the cursor never lingers near an edge long enough
       for the OS to clamp it.

    macOS keeps the original wx-based overlay (``CGDisplayHideCursor``
    has different semantics that integrate cleanly with the overlay);
    only Windows takes this in-process fast path.
    """

    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5

    # Polling interval for the capture loop. 5 ms = 200 Hz, well above
    # every consumer mouse's native polling rate (125-1000 Hz), so we
    # never drop motion samples between ticks. Hot path is one
    # ``GetCursorPos`` + at most one ``SetCursorPos`` syscall.
    _CAPTURE_POLL_INTERVAL = 0.0001

    def __init__(self, *args, **kwargs):
        # Force the pynput win32 filter on: the daemon passes
        # ``filtering=False`` by default which would skip click/scroll
        # suppression entirely — letting a hidden cursor click straight
        # through to the desktop apps below us.
        kwargs["filtering"] = True
        super().__init__(*args, **kwargs)

        # Capture state. ``_cursor_hidden`` doubles as the "is the
        # capture loop running" flag.
        self._cursor_hidden: bool = False
        self._capture_task: Optional[asyncio.Task] = None
        # Centre we re-warp to after every captured delta. Refreshed
        # on every enable_capture so it tracks layout changes.
        self._listening_center: tuple[int, int] = (0, 0)
        # Cached ``user32`` handle so the hot path doesn't re-resolve
        # the DLL on every poll tick.
        self._user32 = ctypes.windll.user32

        # Receive the cross-screen activation request directly: there
        # is no cursor handler subprocess on Windows, so this listener
        # owns the entire enable / disable-capture flow.
        self.event_bus.subscribe(
            event_type=BusEventType.SCREEN_CHANGE_GUARD,
            callback=self._on_screen_change_guard,
            priority=True,
        )
        # Defensive: re-show the cursor if the active client drops out
        # without a clean return-to-server, otherwise the user is
        # stuck with a blank system cursor.
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected_show_cursor,
            priority=True,
        )

    # ------------------------------------------------------------------
    # SCREEN_CHANGE_GUARD: in-process replacement for what the wx-based
    # CursorHandlerWorker does on macOS. Same ordering contract:
    # - going TO client: dispatch ACTIVE_SCREEN_CHANGED FIRST (so the
    #   server-side controller sees the transition), THEN hide + start
    #   the capture loop;
    # - returning FROM client: show cursor + stop the loop FIRST, THEN
    #   dispatch (so the subsequent ``position_cursor`` warp lands on
    #   a visible cursor).
    # ------------------------------------------------------------------

    async def _on_screen_change_guard(
        self, data: Optional[ActiveScreenChangedEvent]
    ) -> None:
        if data is None:
            return
        self._logger.debug(
            f"_on_screen_change_guard active_screen={data.active_screen!r}"
        )
        if data.active_screen:
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=data,
            )
            await asyncio.sleep(0)
            await self._enable_capture()
        else:
            await self._disable_capture()
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=data,
            )

    async def _on_client_disconnected_show_cursor(
        self, data: Optional[ClientDisconnectedEvent]
    ) -> None:
        if self._cursor_hidden:
            await self._disable_capture()

    # ------------------------------------------------------------------
    # Capture lifecycle
    # ------------------------------------------------------------------

    async def _enable_capture(self) -> None:
        """Hide the cursor, warp it to the primary-monitor centre, and
        start the polling capture loop.
        """
        if self._cursor_hidden:
            return
        self._listening_center = self._compute_listening_center()
        cx, cy = self._listening_center
        # Hide cursor globally BEFORE starting the polling loop so the
        # cursor doesn't visibly jitter while the loop is recentring.
        _hide_system_cursors()
        try:
            self._user32.SetCursorPos(cx, cy)
        except Exception as e:
            self._logger.error(f"SetCursorPos centre failed ({e})")
        self._cursor_hidden = True
        try:
            self._capture_task = asyncio.create_task(self._capture_loop())
        except RuntimeError as e:
            self._logger.error(f"Failed to start capture loop ({e})")
        self._logger.debug(f"Capture enabled at centre={self._listening_center}")

    async def _disable_capture(self) -> None:
        """Stop the polling loop and restore the system cursors."""
        if not self._cursor_hidden:
            return
        self._cursor_hidden = False
        task = self._capture_task
        self._capture_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        _restore_system_cursors()
        self._logger.debug("Capture disabled, system cursors restored")

    async def _capture_loop(self) -> None:
        """Poll ``GetCursorPos`` at high frequency, ship deltas, recentre.

        Polling (not pynput's ``on_move``) is the source of truth here.
        ``WH_MOUSE_LL`` only fires when the cursor position actually
        changes, so a cursor pinned against a screen edge by sustained
        user push stops generating events — polling reads the clamped
        position, computes the delta, and warps the cursor back to the
        centre so the next motion intent is captured cleanly.
        """
        cx, cy = self._listening_center
        user32 = self._user32
        point = wintypes.POINT()
        get_pos = user32.GetCursorPos
        set_pos = user32.SetCursorPos
        try:
            while self._cursor_hidden:
                try:
                    if get_pos(ctypes.byref(point)):
                        x, y = point.x, point.y
                        dx = x - cx
                        dy = y - cy
                        if dx or dy:
                            mouse_event = MouseEvent(
                                dx=dx,
                                dy=dy,
                                action=MouseEvent.MOVE_ACTION,
                            )
                            try:
                                await self.stream.send(mouse_event)
                            except Exception as e:
                                self._logger.error(
                                    f"stream.send failed in capture loop ({e})"
                                )
                            try:
                                set_pos(cx, cy)
                            except Exception:
                                pass
                except Exception as e:
                    self._logger.error(f"capture loop tick failed ({e})")
                await asyncio.sleep(self._CAPTURE_POLL_INTERVAL)
        except asyncio.CancelledError:
            pass

    def _compute_listening_center(self) -> tuple[int, int]:
        """Primary-monitor centre, with fallbacks. The polling loop
        warps the cursor here after every captured delta — picking the
        primary monitor keeps the warp target predictable across
        multi-monitor servers.
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
    # on_move: only used outside listening mode; polling owns the
    # delta-capture path while listening.
    # ------------------------------------------------------------------

    def on_move(self, x, y):  # noqa: D401 - matches pynput signature
        if self._listening:
            # Polling captures deltas; suppress any hook-driven path so
            # we don't accidentally double-count or feed back our own
            # recentre warps.
            return True
        return super().on_move(x, y)

    # ------------------------------------------------------------------
    # Native suppression filter — wired up by the base when
    # ``filtering=True`` (which we force in __init__). Suppresses
    # button / scroll events while listening so a hidden cursor can't
    # click through to apps on the server.
    # ------------------------------------------------------------------

    def _win32_mouse_suppress_filter(self, msg, data):
        if self._listening:
            # msg = 513/514 -> left down/up
            # msg = 516/517 -> right down/up
            # msg = 519/520 -> middle down/up
            # msg = 522/523 -> scroll
            if msg in (513, 514, 516, 517, 519, 520, 522, 523):
                self._listener._suppress = True  # ty: ignore
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
