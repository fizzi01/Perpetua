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

import asyncio
import atexit
import ctypes
import ctypes.util
import os
import sys
import threading
from time import monotonic, sleep
from typing import Optional

from Quartz import (
    CGAssociateMouseAndMouseCursorPosition,  # ty:ignore[unresolved-import]
    CGCursorIsVisible,  # ty:ignore[unresolved-import]
    CGDisplayHideCursor,  # ty:ignore[unresolved-import]
    CGDisplayShowCursor,  # ty:ignore[unresolved-import]
    CGEventCreate,  # ty:ignore[unresolved-import]
    CGEventCreateMouseEvent,  # ty:ignore[unresolved-import]
    CGEventGetLocation,  # ty:ignore[unresolved-import]
    CGSetLocalEventsSuppressionInterval,  # ty:ignore[unresolved-import]
    CGWarpMouseCursorPosition,  # ty:ignore[unresolved-import]
    CGEventGetIntegerValueField,  # ty:ignore[unresolved-import]
    CGEventPost,  # ty:ignore[unresolved-import]
    CGEventSetIntegerValueField,  # ty:ignore[unresolved-import]
    CGEventTapEnable,  # ty:ignore[unresolved-import]
    CGMainDisplayID,  # ty:ignore[unresolved-import]
    kCGEventLeftMouseDragged,  # ty:ignore[unresolved-import]
    kCGEventRightMouseDragged,  # ty:ignore[unresolved-import]
    kCGEventOtherMouseDragged,  # ty:ignore[unresolved-import]
    kCGEventMouseMoved,  # ty:ignore[unresolved-import]
    kCGEventTapDisabledByTimeout,  # ty:ignore[unresolved-import]
    kCGEventTapDisabledByUserInput,  # ty:ignore[unresolved-import]
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
from .backend import MouseListener


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
# owns the active window UNLESS the connection has the private
# ``SetsCursorInBackground`` property set - the same trick Barrier/InputLeap use
# to hide the cursor from a background KVM daemon. The CGS* symbols aren't
# exposed by PyObjC, so they're reached via ctypes. Barrier sets this ONCE at
# startup using ``_CGSDefaultConnection()`` and checks the return code; doing it
# lazily-per-hide and swallowing errors is what made the hide silently fail in
# the daemon on macOS 26.
_kCFStringEncodingUTF8 = 0x08000100
_kCGErrorSuccess = 0


def _enable_cursor_hide_in_background() -> bool:
    """Set the private ``SetsCursorInBackground`` property (Barrier recipe).

    Must be called once at startup. Returns True on success. Raises on failure
    so the caller can log a precise reason instead of a silent degrade to a
    background-ineffective ``CGDisplayHideCursor``.
    """
    if sys.platform != "darwin":
        return False

    cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
    cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))

    # _CGSDefaultConnection is the per-thread default connection Barrier uses;
    # fall back to CGSMainConnectionID if it's ever unavailable.
    if hasattr(cg, "_CGSDefaultConnection"):
        conn_fn = cg._CGSDefaultConnection
    else:
        conn_fn = cg.CGSMainConnectionID
    conn_fn.restype = ctypes.c_int
    conn_fn.argtypes = []

    cg.CGSSetConnectionProperty.restype = ctypes.c_int
    cg.CGSSetConnectionProperty.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    true_val = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")

    cid = conn_fn()
    key = cf.CFStringCreateWithCString(
        None, b"SetsCursorInBackground", _kCFStringEncodingUTF8
    )
    if not key:
        raise OSError("CFStringCreateWithCString(SetsCursorInBackground) failed")
    try:
        rc = cg.CGSSetConnectionProperty(cid, cid, key, true_val)
    finally:
        cf.CFRelease(key)
    if rc != _kCGErrorSuccess:
        raise OSError(f"CGSSetConnectionProperty failed (rc={rc})")
    return True


def _disable_local_events_suppression() -> bool:
    """Stop our own warps from freezing our own injected motion.

    After ``CGWarpMouseCursorPosition`` macOS suppresses *local hardware* events
    for ``CGSetLocalEventsSuppressionInterval`` seconds - 0.25 s by default -
    so that a real mouse can't immediately fight an application's warp. Our
    relative injection goes through the HID system, i.e. it *is* local hardware
    input, so the interval suppresses us: measured 257 ms of a dead cursor after
    a single warp, and a crossing warps the landing point repeatedly. That was
    the "cursor frozen for a moment at the crossing point" report.

    Zeroing the interval is the only remedy that works here (measured: 257 ms ->
    ~0 ms). The two alternatives commonly cited both left it at 257 ms on Darwin
    25.5: the per-source
    ``CGEventSourceSetLocalEventsFilterDuringSuppressionState`` (it doesn't cover
    warps, which have no event source) and re-associating right after the warp.
    Deprecated but functional, like ``IOHIDPostEvent`` itself; it only affects
    this process's connection, not other applications.
    """
    try:
        CGSetLocalEventsSuppressionInterval(0.0)
        return True
    except Exception:
        return False


def _hide_cursor() -> None:
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


# --------------------------------------------------------------------------- #
# HID-level relative injection (IOKit / IOHIDSystem)
#
# A CGEvent always carries an absolute location, so posting one MOVES the cursor
# even when the foreground app has called
# CGAssociateMouseAndMouseCursorPosition(False) to lock it - the app's grab is
# bypassed, the pointer drifts out of its window, and clicks land on whatever is
# underneath (the desktop). Withholding the location instead pins the cursor for
# everyone, which freezes it while the user types. There is no way to tell the
# two situations apart: macOS exposes no getter for the association state (the
# question is unanswered on Apple's own forums), and CGCursorIsVisible() reads
# False both for a game's grab and for AppKit's type-in-a-text-field auto-hide.
# Three generations of visibility-based heuristics here all ended up freezing
# the cursor while typing.
#
# IOHIDPostEvent with kIOHIDSetRelativeCursorPosition delivers the delta to the
# IOHIDSystem instead, which is *below* the association logic - the same path a
# physical mouse takes. The OS then decides whether the cursor moves, so both
# cases come out right with no detection at all: a grabbing app gets its deltas
# while its cursor stays put, and while typing the pointer moves (and the OS
# cancels its own auto-hide) exactly as a real mouse would.
#
# The API is deprecated (10.0-11.0) but alive and privilege-clean: it only
# requires the caller's euid to own /dev/console, which holds for a daemon in the
# user's session. Measured on Darwin 25.5: kIOReturnSuccess, 1.00 px per delta
# unit, ~0.6 ms to show up in the cursor position versus ~17 ms for a CGEvent.
# Same approach as ckb-next. If any of it fails we fall back to the CGEvent path.
# --------------------------------------------------------------------------- #

# IOKit/hidsystem/IOHIDShared.h
_kIOHIDParamConnectType = 1
# IOKit/IOLLEvent.h. The dragged variants matter: the HID system posts the event
# type we ask for, it does NOT derive it from the button state, so motion while a
# button is held must be posted as dragged or the drag breaks (measured: a plain
# NX_MOUSEMOVED under a held button comes out as MouseMoved and drops the drag).
_NX_MOUSEMOVED = 5
_NX_LMOUSEDRAGGED = 6
_NX_RMOUSEDRAGGED = 7
_kNXEventDataVersion = 2
# IOKit/hidsystem/IOHIDLib.h
_kIOHIDSetRelativeCursorPosition = 0x00000004
_kIOReturnSuccess = 0
# NXEventData is a union whose exact size varies with the SDK; the callee reads
# a fixed prefix, so an oversized zeroed buffer is always safe.
_NXEVENTDATA_SIZE = 256


class _IOGPoint(ctypes.Structure):
    """IOKit's 16-bit screen point (IOKit/graphics/IOGraphicsTypes.h)."""

    _fields_ = [("x", ctypes.c_int16), ("y", ctypes.c_int16)]


class _NXMouseMove(ctypes.Structure):
    """Head of NXEventData's ``mouseMove`` member (IOKit/IOLLEvent.h)."""

    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("subx", ctypes.c_uint8),
        ("suby", ctypes.c_uint8),
    ]


class _HIDRelativeInjector:
    """Lazy, self-disabling wrapper over an IOHIDSystem param connection.

    ``available`` flips to False the first time anything fails, so the hot path
    never retries a broken connection (and never logs per event) - the caller
    falls back to CGEvents from then on. Every disable records a reason, which
    the caller drains exactly once through ``take_failure``.

    ``IOHIDPostEvent`` is deprecated, so this deliberately does not trust its
    return code alone: ``verify`` checks that the cursor really moved, which is
    what would catch the API being turned into a silent no-op. Without that the
    failure would be invisible - a motionless cursor reads exactly like an app
    holding the pointer, and the CGEvent fallback would never engage.
    """

    def __init__(self, forced_off: Optional[str] = None):
        self._forced_off = forced_off
        self._reset()

    def _reset(self) -> None:
        self._iokit = None
        self._service = 0
        self._connect = 0
        self._opened = False
        self.available = sys.platform == "darwin" and self._forced_off is None
        self.failure: Optional[str] = self._forced_off

    # How long the self-test waits for the injected pixel to show up in the
    # cursor position. Generous on purpose: the read lags the injection by a few
    # milliseconds, and failing a healthy path would silently cost the game
    # fidelity the HID route exists for.
    SELF_TEST_TIMEOUT = 0.05

    def take_failure(self) -> Optional[str]:
        """Return the pending failure reason, once."""
        failure, self.failure = self.failure, None
        return failure

    def _disable(self, reason: str) -> bool:
        """Record why the HID path is out and hand over to the fallback."""
        self.available = False
        self.failure = reason
        return False

    def _open(self) -> bool:
        self._opened = True
        try:
            iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
            iokit.IOServiceMatching.restype = ctypes.c_void_p
            iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
            iokit.IOServiceGetMatchingService.restype = ctypes.c_uint32
            iokit.IOServiceGetMatchingService.argtypes = [
                ctypes.c_uint32,
                ctypes.c_void_p,
            ]
            iokit.IOServiceOpen.restype = ctypes.c_int32
            iokit.IOServiceOpen.argtypes = [
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_uint32),
            ]
            iokit.IOServiceClose.restype = ctypes.c_int32
            iokit.IOServiceClose.argtypes = [ctypes.c_uint32]
            iokit.IOObjectRelease.restype = ctypes.c_int32
            iokit.IOObjectRelease.argtypes = [ctypes.c_uint32]
            iokit.IOHIDPostEvent.restype = ctypes.c_int32
            iokit.IOHIDPostEvent.argtypes = [
                ctypes.c_uint32,
                ctypes.c_uint32,
                _IOGPoint,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_uint32,
            ]

            matching = iokit.IOServiceMatching(b"IOHIDSystem")
            if not matching:
                return self._disable("IOServiceMatching(IOHIDSystem) returned NULL")
            # IOServiceGetMatchingService consumes the matching dictionary.
            service = iokit.IOServiceGetMatchingService(0, matching)
            if not service:
                return self._disable("IOHIDSystem service not found")

            # mach_task_self() is a macro over the mach_task_self_ global.
            libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            task = ctypes.c_uint32.in_dll(libsystem, "mach_task_self_").value

            connect = ctypes.c_uint32(0)
            rc = iokit.IOServiceOpen(
                service, task, _kIOHIDParamConnectType, ctypes.byref(connect)
            )
            if rc != _kIOReturnSuccess or not connect.value:
                iokit.IOObjectRelease(service)
                return self._disable(f"IOServiceOpen returned 0x{rc & 0xFFFFFFFF:08X}")

            self._iokit = iokit
            self._service = service
            self._connect = connect.value
            return True
        except Exception as e:
            return self._disable(f"IOServiceOpen failed - {type(e).__name__}: {e}")

    def post(self, dx: int, dy: int, event_type: int = _NX_MOUSEMOVED) -> bool:
        """Post one relative motion event. False means "use the fallback".

        ``event_type`` must be a dragged variant while a button is held.
        """
        if not self.available:
            return False
        if not self._opened and not self._open():
            return False
        if self._iokit is None:
            # Reachable only if the connection was closed under us; without a
            # reason here the degradation would be silent.
            return self._disable("IOKit connection is not open")
        try:
            buf = (ctypes.c_uint8 * _NXEVENTDATA_SIZE)()
            move = ctypes.cast(buf, ctypes.POINTER(_NXMouseMove)).contents
            move.dx = int(dx)
            move.dy = int(dy)
            rc = self._iokit.IOHIDPostEvent(
                self._connect,
                event_type,
                _IOGPoint(0, 0),
                ctypes.byref(buf),
                _kNXEventDataVersion,
                0,
                _kIOHIDSetRelativeCursorPosition,
            )
        except Exception as e:
            return self._disable(f"IOHIDPostEvent failed - {type(e).__name__}: {e}")
        if rc != _kIOReturnSuccess:
            return self._disable(f"IOHIDPostEvent returned 0x{rc & 0xFFFFFFFF:08X}")
        return True

    def verify(self, read_position) -> bool:
        """Check that a posted delta actually moves the cursor.

        ``IOHIDPostEvent`` is deprecated: the failure mode to fear is not an
        error code but a silent no-op, and that one is invisible from here - a
        cursor that never moves is indistinguishable from an app holding the
        pointer, so the fallback would never engage and the cursor would simply
        stay dead. One pixel out and back settles it. A failure here is not
        fatal: it just means the CGEvent path takes over.

        ``read_position`` is injected rather than imported so this stays a
        property of the caller's coordinate source.
        """
        if not self.available:
            return False
        before = read_position()
        if before is None:
            return self._disable("cannot read the cursor position to self-test")
        if not self.post(1, 0):
            return False

        # The position takes a few milliseconds to reflect an injected delta
        # (measured: unchanged at 2 ms, changed by 10 ms), so a single immediate
        # read would fail a perfectly good path. Poll instead, and leave as soon
        # as it moves - the healthy case costs a few milliseconds, the broken
        # one the whole window, and only at init or on activation.
        deadline = monotonic() + self.SELF_TEST_TIMEOUT
        moved = False
        while not moved and monotonic() < deadline:
            after = read_position()
            if after is None:
                self.post(-1, 0)
                return self._disable("cannot read the cursor position to self-test")
            moved = after != before
            if not moved:
                sleep(0.002)

        # Put it back before judging: the probe must not leave the cursor moved
        # even when it worked.
        self.post(-1, 0)
        if not moved:
            return self._disable("IOHIDPostEvent succeeded but the cursor did not move")
        return True

    def retry(self, read_position) -> bool:
        """Re-attempt a disabled HID path. Never called from the hot path.

        A failure at daemon start (login window, fast user switching) must not
        pin the whole session to the fallback, so activation re-arms it once.
        """
        if self.available:
            return True
        if self._forced_off is not None:
            return False
        self.close()
        self._reset()
        return self.verify(read_position)

    def close(self) -> None:
        try:
            if self._iokit is not None:
                if self._connect:
                    self._iokit.IOServiceClose(self._connect)
                if self._service:
                    self._iokit.IOObjectRelease(self._service)
        except Exception:
            pass
        finally:
            self._connect = 0
            self._service = 0
            self._iokit = None
            # Posting on a closed connection would target io_connect_t 0.
            self.available = False


# Escape hatch: run the client on the CGEvent path as if IOKit were gone. It is
# what makes the fallback verifiable end-to-end instead of a code path nobody
# has ever exercised - and IOHIDPostEvent being deprecated makes that path a
# question of when, not if.
FORCE_CGEVENT_ENV_VAR = "PERPETUA_MOUSE_FORCE_CGEVENT"


def _forced_cgevent_reason() -> Optional[str]:
    if os.environ.get(FORCE_CGEVENT_ENV_VAR) == "1":
        return f"forced by {FORCE_CGEVENT_ENV_VAR}=1"
    return None


_hid_injector = _HIDRelativeInjector(_forced_cgevent_reason())

if sys.platform == "darwin":
    atexit.register(_hid_injector.close)


# Mouse-move events that carry HID deltas we forward to the active client.
_DARWIN_DELTA_EVENT_TYPES = (
    kCGEventMouseMoved,
    kCGEventLeftMouseDragged,
    kCGEventRightMouseDragged,
    kCGEventOtherMouseDragged,
)


class _DarwinMouseListener(MouseListener):
    """pynput mouse Listener that stashes its CGEventTap mach port.

    pynput enables the tap once and never re-enables it, and keeps the port only
    as a local in ``_run``. We need the port to re-enable the tap after the
    kernel disables it (``kCGEventTapDisabledByTimeout``), so capture survives
    load spikes / App Nap instead of silently dying.
    """

    def _create_event_tap(self):
        tap = super()._create_event_tap()
        self._perpetua_tap = tap
        return tap


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

    # How often to re-assert the hidden cursor while a client is active. The
    # WindowServer re-shows the cursor on Mission Control / Spaces / unlock; a
    # short poll re-hides it (the old wx overlay used a 500ms lock monitor).
    _REASSERT_INTERVAL = 0.1
    # Safety cap for the balanced restore loop (see _restore_cursor).
    _RESTORE_SHOW_CAP = 32

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

        # Re-assert task: re-hides the cursor after the WindowServer re-shows it
        # (Mission Control / Spaces / unlock), lives only while a client active.
        self._reassert_task: Optional[asyncio.Task] = None

        # Enable background cursor hiding ONCE at startup (Barrier recipe). If
        # this fails, CGDisplayHideCursor is a no-op from a background daemon, so
        # log the outcome explicitly rather than silently degrading.
        self._bg_hide_enabled: bool = False
        if sys.platform == "darwin":
            try:
                self._bg_hide_enabled = _enable_cursor_hide_in_background()
                self._logger.debug(
                    "SetsCursorInBackground applied",
                    enabled=self._bg_hide_enabled,
                )
            except Exception as e:
                self._logger.debug(
                    "SetsCursorInBackground failed - cursor hide will not work "
                    "while the daemon is in the background",
                    error=str(e),
                )

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
            self._start_reassert()
            await self.event_bus.dispatch(
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=data,
            )
        else:
            # Re-couple BEFORE the dispatch so the controller's absolute warp to
            # the return point isn't fought by a decoupled cursor.
            self._stop_reassert()
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
            self._stop_reassert()
            self._unpin()
            self._restore_cursor()

    def stop(self) -> bool:
        # Defensive teardown on a clean listener stop: never leave the cursor
        # hidden or the mouse decoupled if we're torn down mid-control.
        try:
            self._stop_reassert()
            if self._cursor_hidden:
                self._unpin()
                self._restore_cursor()
        except Exception as e:
            self._logger.error("error during mouse listener teardown", error=str(e))
        return super().stop()

    def _hide_and_pin(self) -> None:
        """Hide the cursor and decouple the mouse. Synchronous and idempotent."""
        if self._cursor_hidden:
            return
        self._cursor_hidden = True
        try:
            # Hide BEFORE decoupling: decoupling freezes the cursor, and a frozen
            # cursor won't composite the blank frame until the next motion, which
            # adds a visible delay. Hiding while the pointer is still moving into
            # the edge lets the next motion frame render the blank immediately.
            _hide_cursor()
            _decouple_mouse()
        except Exception as e:
            self._logger.error("failed to hide/pin cursor", error=str(e))

    def _unpin(self) -> None:
        """Re-couple the physical mouse to the cursor. Idempotent."""
        try:
            _recouple_mouse()
        except Exception as e:
            self._logger.error("failed to re-couple mouse", error=str(e))

    def _restore_cursor(self) -> None:
        """Reveal the cursor again, balancing the hide/show counter.

        ``CGDisplayHideCursor`` is a per-connection counter: every re-assert
        that re-hid the cursor (after the WindowServer re-showed it) bumped it,
        so a single ``CGDisplayShowCursor`` could leave the cursor stuck
        invisible. Show in a loop until the cursor is actually visible;
        ``CGDisplayShowCursor`` at count 0 is a no-op, so this is safe.
        """
        if not self._cursor_hidden:
            return
        self._cursor_hidden = False
        try:
            for _ in range(self._RESTORE_SHOW_CAP):
                if CGCursorIsVisible():
                    break
                _show_cursor()
        except Exception as e:
            self._logger.error("failed to show cursor", error=str(e))

    def _start_reassert(self) -> None:
        """Start the periodic re-assert loop while a client is active."""
        if self._reassert_task is not None and not self._reassert_task.done():
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            self._reassert_task = loop.create_task(self._reassert_loop())
        except RuntimeError:
            self._reassert_task = None

    def _stop_reassert(self) -> None:
        task = self._reassert_task
        self._reassert_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _reassert_loop(self) -> None:
        try:
            while self._cursor_hidden:
                await asyncio.sleep(self._REASSERT_INTERVAL)
                if not self._cursor_hidden:
                    break
                try:
                    # Re-hide only when the WindowServer has re-shown the cursor,
                    # so the hide counter doesn't grow every tick.
                    if CGCursorIsVisible():
                        _hide_cursor()
                        _decouple_mouse()
                except Exception as e:
                    self._logger.error("cursor re-assert failed", error=str(e))
        except asyncio.CancelledError:
            pass

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

    def _create_listener(self):
        # Use our subclass so we can grab the CGEventTap port and re-enable it
        # after a kernel-initiated disable (timeout). Same args as the base.
        return _DarwinMouseListener(
            on_move=self.on_move,
            on_scroll=self.on_scroll,
            on_click=self.on_click,
            **self._filter_args,
        )

    def _on_tap_disabled_by_user_input(self) -> None:
        # Runs on the event loop: the tap was deliberately killed (secure input
        # on a password field, or Accessibility revoked mid-session). We can't
        # recover the tap here, so make sure the user isn't stranded with a
        # hidden cursor / frozen mouse. The permission watchdog handles an actual
        # TCC revocation from here on.
        self._stop_reassert()
        if self._cursor_hidden:
            self._unpin()
            self._restore_cursor()

    def on_move(self, x, y):
        # While a client is active the suppress filter owns the delta-capture
        # path; skip the base edge-detection work entirely.
        if self._listening:
            return True
        result = super().on_move(x, y)
        # If super() committed a screen crossing on THIS event it set
        # ``_handling_cross_screen`` synchronously (before scheduling the async
        # handler). Hide right here, on the pynput thread, instead of waiting for
        # the loop round-trip -> event-bus -> _on_screen_change_guard chain: that
        # chain's scheduling jitter is what left an intermittent hide lag. The
        # guard still runs _hide_and_pin() (idempotent) + starts the re-assert.
        if self._handling_cross_screen and not self._cursor_hidden:
            self._hide_and_pin()
        return result

    def _darwin_mouse_suppress_filter(self, event_type, event):
        """pynput ``darwin_intercept``: called after on_move/on_click/on_scroll.

        Returning ``event`` passes it to the local desktop, ``None`` suppresses
        it. While listening we read the HID delta off move/drag events, forward
        it to the client, and swallow EVERY mouse event so the local machine
        never sees the movement or the (client-bound) clicks/scroll.
        """
        # Tap lifecycle events (delivered even when not listening). pynput never
        # re-enables the tap itself, and the permission watchdog can't see these
        # (they aren't permission changes), so we handle them here.
        if event_type == kCGEventTapDisabledByTimeout:
            # Kernel disabled the tap because a callback ran too long (load /
            # App Nap / suspension). Re-enable in place and keep capture state.
            tap = getattr(self._listener, "_perpetua_tap", None)
            if tap is not None:
                try:
                    CGEventTapEnable(tap, True)
                    self._logger.warning("event tap disabled by timeout - re-enabled")
                except Exception as e:
                    self._logger.error("failed to re-enable event tap", error=str(e))
            else:
                self._logger.error("event tap disabled by timeout but port unavailable")
            return event
        if event_type == kCGEventTapDisabledByUserInput:
            # Deliberate kill: secure-input (password field) or TCC revoked
            # mid-session. Not recoverable here - show the cursor + re-couple so
            # the user isn't stuck, then tear down capture on the loop.
            self._logger.error("event tap disabled by user input - releasing capture")
            # Clear the flag first so the re-assert loop stops re-hiding, then
            # recouple and show (balanced loop - the counter may be >1 from
            # re-asserts) so the cursor can't stay stuck invisible.
            self._cursor_hidden = False
            try:
                _recouple_mouse()
                for _ in range(self._RESTORE_SHOW_CAP):
                    if CGCursorIsVisible():
                        break
                    _show_cursor()
            except Exception as e:
                self._logger.error("failed to release cursor on tap kill", error=str(e))
            loop = self._loop
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(self._on_tap_disabled_by_user_input)
            return event

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Must happen before the first warp AND before the self-test below: see
        # the function's docstring for why our own warps would otherwise freeze
        # our own injected motion (and would make the self-test read a cursor
        # that legitimately did not move).
        ok = _disable_local_events_suppression()
        self._logger.info("local events suppression disabled", ok=ok)
        _hid_injector.verify(self._cursor_position)
        self._log_injection_mode()

    def _log_injection_mode(self) -> None:
        """State the active injection path, and why, whenever it changes."""
        failure = _hid_injector.take_failure()
        if _hid_injector.available:
            self._logger.info("mouse injection path", mode="hid")
        else:
            self._logger.warning(
                "mouse injection degraded to CGEvent",
                mode="cgevent",
                reason=failure or "unknown",
            )

    def _on_relative_injection_degraded(self) -> None:
        """Retry the HID path once per activation, never on the hot path."""
        was_available = _hid_injector.available
        _hid_injector.retry(self._cursor_position)
        if _hid_injector.available != was_available or _hid_injector.failure:
            self._log_injection_mode()

    def _cursor_position(self) -> Optional[tuple[float, float]]:
        """Cursor position according to the *event system*.

        pynput reads ``NSEvent.mouseLocation()`` (an AppKit-level value, defined
        in terms of the last event, and flipped against the main display's
        height). Injection here happens at the HID/event-system level, so the
        read is taken from the same place - ``CGEventGetLocation`` on a fresh
        event, which is what Deskflow uses for exactly this reason.
        """
        try:
            loc = CGEventGetLocation(CGEventCreate(None))
            return float(loc.x), float(loc.y)
        except Exception as e:
            self._logger.error("failed to read cursor position", error=str(e))
            return None

    def _warp_cursor(self, x: float | int, y: float | int) -> None:
        """Place the cursor without generating a mouse event.

        ``CGWarpMouseCursorPosition`` is the documented way to move the pointer
        for its own sake: unlike posting a ``MouseMoved`` CGEvent (what pynput's
        position setter does) it emits nothing, so a landing no longer injects a
        burst of synthetic movement into whatever app is focused - a game would
        read those as camera input. It also still works while an app has
        dissociated the cursor.

        The measurement state is dropped for the same reason as in the base
        class: a warp is not travel, and whether the OS was withholding our
        motion before it is no longer known.
        """
        self._last_seen_pos = None
        self._immobile_moves = 0
        CGWarpMouseCursorPosition((float(x), float(y)))

    def _inject_relative(self, dx: int, dy: int) -> tuple[int, int]:
        """Deliver relative motion the way a physical mouse does.

        The delta goes to the ``IOHIDSystem`` via ``IOHIDPostEvent``, which sits
        *below* ``CGAssociateMouseAndMouseCursorPosition``, so the OS - not us -
        decides whether the visible cursor moves. That single property is what
        makes both problem cases come out right without detecting anything:

        - an app that grabbed the pointer receives the deltas while its cursor
          stays exactly where it put it (measured: 0 px of movement during a
          burst, versus 400 px through a CGEvent, which bypasses the grab and is
          how the pointer used to drift out of a game's window);
        - while the user types, the pointer moves and macOS cancels its own
          "hidden until the mouse moves" auto-hide, as with any real mouse. No
          pinning, so nothing can freeze.

        A held button changes only the event *type*, never the path: the HID
        system posts what we ask for rather than deriving it from the button
        state, so motion while dragging goes out as ``NX_?MOUSEDRAGGED`` and the
        drag survives (verified with an event tap: the OS delivers
        ``LeftMouseDragged``, and it works even though the press itself came
        through a CGEvent). Falling back to a CGEvent for drags instead - which
        is what this did at first - reintroduced the absolute position, so
        holding a button in a game made the grabbed cursor drift again.

        The return value is the displacement the cursor *actually took*, which
        the OS decides here: it is measured from the position observed at the
        previous injection, so a grabbed (immobile) cursor correctly reports no
        travel to ``_accumulate_inward_travel``.
        """
        pos = self._cursor_position()
        applied = (0, 0)
        if pos is not None:
            if self._last_seen_pos is not None:
                applied = (
                    round(pos[0] - self._last_seen_pos[0]),
                    round(pos[1] - self._last_seen_pos[1]),
                )
                # Count how long the OS has been withholding our motion, so
                # edge routing can stand down while an app holds the pointer
                # (``IMMOBILE_MOVES_BEFORE_HOLD``). Only a *requested* move
                # that produced nothing counts; without a baseline we don't
                # know, so we don't guess. And a cursor already against a
                # bound does not move when pushed further that way - that
                # immobility is geometric, not a grab, and counting it would
                # suspend edge routing exactly while the user is pushing at
                # the edge to hand control back to the server.
                if (
                    applied == (0, 0)
                    and (dx or dy)
                    and not self._motion_is_bounded(pos[0], pos[1], dx, dy)
                ):
                    self._immobile_moves += 1
                else:
                    self._immobile_moves = 0
            self._last_seen_pos = pos

        dragging = self._pressed and self._is_dragging
        right_drag = dragging and self._previous_button == ButtonMapping.right.value
        if dragging:
            hid_type = _NX_RMOUSEDRAGGED if right_drag else _NX_LMOUSEDRAGGED
        else:
            hid_type = _NX_MOUSEMOVED

        if _hid_injector.post(dx, dy, hid_type):
            return applied
        if _hid_injector.failure is not None:
            # Drains the reason, so this reports once per degradation rather
            # than once per event.
            self._log_injection_mode()

        try:
            if pos is None:
                return super()._inject_relative(dx, dy)
            # Clamp to the desktop. A CGEvent carries an absolute location, and
            # the position we read back is the one we posted, not the one the
            # cursor ended up at: pushing past a screen edge would compound
            # ``pos + delta`` every event. The HID path needs
            # none of this - there the OS owns the position and it stays put.
            min_x, min_y, max_x, max_y = self._screen_bbox
            new_x = max(min_x, min(max_x - 1, pos[0] + dx))
            new_y = max(min_y, min(max_y - 1, pos[1] + dy))

            if dragging:
                if right_drag:
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
            return applied
        except Exception as e:
            self._logger.error("relative CGEvent injection failed", error=str(e))
            return super()._inject_relative(dx, dy)
