"""
Provides mouse input support for macOS (Darwin) systems.
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

import atexit
import ctypes
import ctypes.util
import sys
import threading
from typing import Optional

from Quartz import (
    CGAssociateMouseAndMouseCursorPosition,  # ty:ignore[unresolved-import]
    CGCursorIsVisible,  # ty:ignore[unresolved-import]
    CGDisplayHideCursor,  # ty:ignore[unresolved-import]
    CGDisplayShowCursor,  # ty:ignore[unresolved-import]
    CGEventCreateMouseEvent,  # ty:ignore[unresolved-import]
    CGEventGetIntegerValueField,  # ty:ignore[unresolved-import]
    CGEventPost,  # ty:ignore[unresolved-import]
    CGEventSetIntegerValueField,  # ty:ignore[unresolved-import]
    CGMainDisplayID,  # ty:ignore[unresolved-import]
    kCGEventLeftMouseDragged,  # ty:ignore[unresolved-import]
    kCGEventRightMouseDragged,  # ty:ignore[unresolved-import]
    kCGEventOtherMouseDragged,  # ty:ignore[unresolved-import]
    kCGEventMouseMoved,  # ty:ignore[unresolved-import]
    kCGHIDEventTap,  # ty:ignore[unresolved-import]
    kCGMouseButtonLeft,  # ty:ignore[unresolved-import]
    kCGMouseButtonRight,  # ty:ignore[unresolved-import]
    kCGMouseEventDeltaX,  # ty:ignore[unresolved-import]
    kCGMouseEventDeltaY,  # ty:ignore[unresolved-import]
)

from event import (
    BusEventType,
    MouseEvent,
    ActiveScreenChangedEvent,
    ClientDisconnectedEvent,
)
from input.utils import ButtonMapping

from . import _base


# --------------------------------------------------------------------------- #
# Native cursor control (Quartz / CoreGraphics)
#
# On Windows the mouse listener hides the cursor system-wide with
# SetSystemCursor and pins it with ClipCursor; on macOS the equivalents are
# CGDisplayHideCursor plus CGAssociateMouseAndMouseCursorPosition(False), which
# decouples the physical mouse from the on-screen cursor so the pointer stays
# put while the HID keeps emitting deltas. There is no overlay window / separate
# process anymore (see input/cursor/_darwin.py, now a no-op stub).
# --------------------------------------------------------------------------- #

# CGDisplayHideCursor only affects the visible cursor when the calling process
# owns the active window UNLESS the connection has SetsCursorInBackground set.
# That property is a private CoreGraphics SPI reached via ctypes (PyObjC doesn't
# expose the CGS* symbols). Resolved lazily so importing this module never
# touches the private framework symbols.
_CGS_STATE: dict = {}
_kCFStringEncodingUTF8 = 0x08000100


def _enable_cursor_hide_in_background() -> None:
    """Best-effort: let CGDisplayHideCursor work from a background process.

    Sets the private ``SetsCursorInBackground`` connection property once. Any
    failure (SPI missing/renamed on a given macOS release) is swallowed - the
    caller falls back to warping the (decoupled) cursor off-screen.
    """
    if sys.platform != "darwin":
        return
    if _CGS_STATE.get("applied"):
        return
    try:
        cg = _CGS_STATE.get("cg")
        cf = _CGS_STATE.get("cf")
        if cg is None:
            cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
            cg.CGSMainConnectionID.restype = ctypes.c_int
            cg.CGSMainConnectionID.argtypes = []
            cg.CGSSetConnectionProperty.restype = ctypes.c_int
            cg.CGSSetConnectionProperty.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            _CGS_STATE["cg"] = cg
        if cf is None:
            cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
            cf.CFStringCreateWithCString.restype = ctypes.c_void_p
            cf.CFStringCreateWithCString.argtypes = [
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.c_uint32,
            ]
            cf.CFRelease.argtypes = [ctypes.c_void_p]
            _CGS_STATE["cf"] = cf
            _CGS_STATE["true"] = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")

        cid = cg.CGSMainConnectionID()
        key = cf.CFStringCreateWithCString(
            None, b"SetsCursorInBackground", _kCFStringEncodingUTF8
        )
        try:
            cg.CGSSetConnectionProperty(cid, cid, key, _CGS_STATE["true"])
        finally:
            if key:
                cf.CFRelease(key)
        _CGS_STATE["applied"] = True
    except Exception:
        # Leave "applied" unset so a later attempt can retry.
        pass


def _hide_cursor() -> None:
    _enable_cursor_hide_in_background()
    CGDisplayHideCursor(CGMainDisplayID())


def _show_cursor() -> None:
    CGDisplayShowCursor(CGMainDisplayID())


def _decouple_mouse() -> None:
    """Pin the on-screen cursor: HID motion no longer moves the pointer."""
    CGAssociateMouseAndMouseCursorPosition(False)


def _recouple_mouse() -> None:
    CGAssociateMouseAndMouseCursorPosition(True)


def _restore_cursor_state() -> None:
    """Reveal the cursor and re-couple the mouse. Idempotent and crash-safe.

    A stuck decouple freezes the physical mouse system-wide, so this MUST run on
    any clean exit (atexit + panic quit + client disconnect). SIGKILL bypasses
    atexit and cannot be covered.
    """
    try:
        CGAssociateMouseAndMouseCursorPosition(True)
    except Exception:
        pass
    try:
        CGDisplayShowCursor(CGMainDisplayID())
    except Exception:
        pass


if sys.platform == "darwin":
    # If we crash while a client is active the user would otherwise be stuck
    # with a hidden, frozen cursor until reboot.
    atexit.register(_restore_cursor_state)


# Mouse-move events that carry HID deltas we forward to the active client.
_DARWIN_DELTA_EVENT_TYPES = (
    kCGEventMouseMoved,
    kCGEventLeftMouseDragged,
    kCGEventRightMouseDragged,
    kCGEventOtherMouseDragged,
)


class ServerMouseListener(_base.ServerMouseListener):
    """
    It listens for mouse events on macOS systems.

    While a client is active it owns the real cursor natively: hides it
    (CGDisplayHideCursor) and pins it (CGAssociateMouseAndMouseCursorPosition),
    reads HID deltas straight off the pynput event tap and forwards them on the
    MOUSE stream, and swallows every local mouse event so nothing leaks to the
    desktop. Clicks/scroll are forwarded to the client by the base ``on_click``/
    ``on_scroll`` handlers, which pynput dispatches before the suppress filter.
    """

    def __init__(self, *args, **kwargs):
        # Force filtering on: the daemon passes filtering=False by default,
        # which would let a hidden cursor click through to the local desktop.
        kwargs["filtering"] = True
        super().__init__(*args, **kwargs)

        self._cursor_hidden: bool = False

        # Coalescing buffer for HID deltas: a high-rate mouse generates ~1 kHz
        # move events on the tap thread, but we enqueue a single drain onto the
        # loop between asyncio ticks (mirrors the Windows Raw Input path).
        self._pending_lock = threading.Lock()
        self._pending_dx = 0
        self._pending_dy = 0
        self._pending_scheduled = False

        self.event_bus.subscribe(
            event_type=BusEventType.SCREEN_CHANGE_GUARD,
            callback=self._on_screen_change_guard,
            priority=True,
        )
        # Restore the cursor if the active client drops without a clean
        # return-to-server, otherwise the user is stuck with a hidden cursor
        # and a frozen (decoupled) mouse.
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected_show_cursor,
            priority=True,
        )

    async def _on_screen_change_guard(
        self, data: Optional[ActiveScreenChangedEvent]
    ) -> None:
        # Going to client: hide+pin first (synchronously, so the cursor
        # disappears immediately, not behind the dispatch latency), then
        # dispatch ACTIVE_SCREEN_CHANGED (which flips ``_listening`` on so the
        # suppress filter starts forwarding deltas). Returning: re-couple the
        # mouse, let the controller warp the cursor to the exact return point
        # during the dispatch while still hidden, and only then reveal it - so
        # it never flashes before jumping to the return position.
        if data is None:
            return

        if data.active_screen:
            self._hide_and_pin()
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=data,
            )
        else:
            # Re-couple BEFORE the dispatch so the controller's absolute warp to
            # the return point isn't fought by a decoupled cursor.
            self._unpin()
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=data,
            )
            self._restore_cursor()

    async def _on_client_disconnected_show_cursor(
        self, data: Optional[ClientDisconnectedEvent]
    ) -> None:
        if self._cursor_hidden:
            self._unpin()
            self._restore_cursor()

    def _hide_and_pin(self) -> None:
        """Hide the cursor and decouple the mouse. Synchronous and idempotent."""
        if self._cursor_hidden:
            return
        self._cursor_hidden = True
        try:
            _decouple_mouse()
            _hide_cursor()
        except Exception as e:
            self._logger.error("failed to hide/pin cursor", error=str(e))

    def _unpin(self) -> None:
        """Re-couple the physical mouse to the cursor. Idempotent."""
        try:
            _recouple_mouse()
        except Exception as e:
            self._logger.error("failed to re-couple mouse", error=str(e))

    def _restore_cursor(self) -> None:
        """Reveal the cursor again. Idempotent."""
        if not self._cursor_hidden:
            return
        self._cursor_hidden = False
        try:
            _show_cursor()
        except Exception as e:
            self._logger.error("failed to show cursor", error=str(e))

    def _enqueue_delta(self, dx: int, dy: int) -> None:
        with self._pending_lock:
            self._pending_dx += dx
            self._pending_dy += dy
            already_scheduled = self._pending_scheduled
            self._pending_scheduled = True
        if already_scheduled:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            with self._pending_lock:
                self._pending_scheduled = False
            return
        try:
            loop.call_soon_threadsafe(self._drain_pending)
        except RuntimeError:
            with self._pending_lock:
                self._pending_scheduled = False

    def _drain_pending(self) -> None:
        with self._pending_lock:
            dx = self._pending_dx
            dy = self._pending_dy
            self._pending_dx = 0
            self._pending_dy = 0
            self._pending_scheduled = False
        if not (dx or dy):
            return
        if not self._cursor_hidden:
            # A final delta arrived after the cursor was restored.
            return
        # send_nowait skips create_task + an event-loop tick vs. awaiting
        # stream.send; if the queue is saturated dropping is the right
        # behaviour on this hot path.
        if not self.stream.send_nowait(
            MouseEvent(dx=dx, dy=dy, action=MouseEvent.MOVE_ACTION)
        ):
            self._logger.warning("Mouse stream queue full, dropped delta")

    def on_move(self, x, y):
        # While a client is active the suppress filter owns the delta-capture
        # path; skip the base edge-detection work entirely.
        if self._listening:
            return True
        return super().on_move(x, y)

    def _darwin_mouse_suppress_filter(self, event_type, event):
        """pynput ``darwin_intercept``: called after on_move/on_click/on_scroll.

        Returning ``event`` passes it to the local desktop, ``None`` suppresses
        it. While listening we read the HID delta off move/drag events, forward
        it to the client, and swallow EVERY mouse event so the local machine
        never sees the movement or the (client-bound) clicks/scroll.
        """
        if not self._listening:
            return event

        if event_type in _DARWIN_DELTA_EVENT_TYPES:
            try:
                dx = CGEventGetIntegerValueField(event, kCGMouseEventDeltaX)
                dy = CGEventGetIntegerValueField(event, kCGMouseEventDeltaY)
            except Exception:
                dx = dy = 0
            if dx or dy:
                self._enqueue_delta(int(dx), int(dy))

        # Suppress every mouse event locally while a client is active.
        return None


class ServerMouseController(_base.ServerMouseController):
    """
    It controls the mouse on macOS systems.
    Its main purpose is to move the cursor and simulate mouse clicks.
    """

    pass


class ClientMouseController(_base.ClientMouseController):
    """
    It controls the mouse on macOS systems.
    Its main purpose is to move the cursor and simulate mouse clicks.
    """

    def _cursor_is_hidden(self) -> bool:
        """True when the system cursor is hidden (game pointer lock).

        Read-only: a foreground game hides the cursor when it grabs the
        pointer. We never hide/show it ourselves.
        """
        return not CGCursorIsVisible()

    def _inject_relative(self, dx: int, dy: int) -> None:
        """Post a genuine relative-motion CGEvent so games read the delta.

        pynput's ``Controller.move`` warps the cursor to an absolute
        position; first-person games reading ``kCGMouseEventDeltaX/Y`` see
        nothing that way. Normally we move the system cursor to
        ``current + delta`` (so the visible pointer tracks on the desktop)
        *and* stamp the event's delta fields, which is what the game's camera
        consumes. Under a game pointer lock (``_pointer_locked``) the game
        pins/centers the cursor itself, so we keep the event at the *current*
        position — only the delta fields carry movement — otherwise our
        absolute point would drag the pinned cursor around. During a drag the
        motion must be delivered as a ``…MouseDragged`` event, not
        ``MouseMoved``, or the drag breaks.
        """
        try:
            cur_x, cur_y = self._controller.position
            if self._pointer_locked:
                new_x, new_y = cur_x, cur_y
            else:
                new_x = cur_x + dx
                new_y = cur_y + dy

            if self._pressed and self._is_dragging:
                if self._previous_button == ButtonMapping.right.value:
                    event_type = kCGEventRightMouseDragged
                    button = kCGMouseButtonRight
                else:
                    event_type = kCGEventLeftMouseDragged
                    button = kCGMouseButtonLeft
            else:
                event_type = kCGEventMouseMoved
                button = kCGMouseButtonLeft

            event = CGEventCreateMouseEvent(None, event_type, (new_x, new_y), button)
            CGEventSetIntegerValueField(event, kCGMouseEventDeltaX, int(dx))
            CGEventSetIntegerValueField(event, kCGMouseEventDeltaY, int(dy))
            CGEventPost(kCGHIDEventTap, event)
        except Exception as e:
            self._logger.error("relative CGEvent injection failed", error=str(e))
            super()._inject_relative(dx, dy)
