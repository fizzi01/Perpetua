"""
Feasibility probe for HID-level relative mouse injection on macOS.

Standalone diagnostic - imports nothing from the Perpetua daemon and changes no
state beyond moving the cursor. It answers one question: can we deliver relative
mouse motion through ``IOHIDPostEvent`` (the path a physical mouse takes, below
the CGAssociateMouseAndMouseCursorPosition logic) instead of through CGEvents
carrying an absolute position (the path that fights a game's cursor lock)?

Run it twice:

    python scripts/probe_hid_inject_macos.py                 # on the desktop
    python scripts/probe_hid_inject_macos.py --game 8        # with a game focused
    python scripts/probe_hid_inject_macos.py --trace-game 8  # passive dx/dy trace

The second form counts down so you can focus the game, then injects a burst and
reports whether the *system cursor* stayed put. A game that dissociated the
cursor must see the camera move while the cursor does not - that is the proof
that the association is being honoured. The trace form injects nothing: it only
logs physical mouse dx/dy and the cursor positions while the game is focused.
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

import argparse
import atexit
import ctypes
import ctypes.util
import os
import pwd
import sys
import time

if sys.platform != "darwin":
    raise SystemExit("this probe is macOS-only")

from AppKit import NSEvent  # noqa: E402  ty:ignore[unresolved-import]
from pynput.mouse import Controller as _PynputController  # noqa: E402
from Quartz import (  # noqa: E402
    CGAssociateMouseAndMouseCursorPosition,  # ty:ignore[unresolved-import]
    CGDisplayPixelsHigh,  # ty:ignore[unresolved-import]
    CGEventCreate,  # ty:ignore[unresolved-import]
    CGEventCreateMouseEvent,  # ty:ignore[unresolved-import]
    CGEventGetIntegerValueField,  # ty:ignore[unresolved-import]
    CGEventGetLocation,  # ty:ignore[unresolved-import]
    CGEventPost,  # ty:ignore[unresolved-import]
    CGEventSetIntegerValueField,  # ty:ignore[unresolved-import]
    CGEventSourceCreate,  # ty:ignore[unresolved-import]
    CGEventSourceSetLocalEventsFilterDuringSuppressionState,  # ty:ignore[unresolved-import]
    CGDisplayBounds,  # ty:ignore[unresolved-import]
    CGMainDisplayID,  # ty:ignore[unresolved-import]
    CGEventTapCreate,  # ty:ignore[unresolved-import]
    CGEventTapEnable,  # ty:ignore[unresolved-import]
    CGSetLocalEventsSuppressionInterval,  # ty:ignore[unresolved-import]
    CGWarpMouseCursorPosition,  # ty:ignore[unresolved-import]
    CFMachPortCreateRunLoopSource,  # ty:ignore[unresolved-import]
    CFRunLoopAddSource,  # ty:ignore[unresolved-import]
    CFRunLoopGetCurrent,  # ty:ignore[unresolved-import]
    CFRunLoopRunInMode,  # ty:ignore[unresolved-import]
    kCFRunLoopCommonModes,  # ty:ignore[unresolved-import]
    kCFRunLoopDefaultMode,  # ty:ignore[unresolved-import]
    kCGEventFilterMaskPermitAllEvents,  # ty:ignore[unresolved-import]
    kCGAnnotatedSessionEventTap,  # ty:ignore[unresolved-import]
    kCGSessionEventTap,  # ty:ignore[unresolved-import]
    kCGEventTapOptionListenOnly,  # ty:ignore[unresolved-import]
    kCGTailAppendEventTap,  # ty:ignore[unresolved-import]
    kCGEventMouseMoved,  # ty:ignore[unresolved-import]
    kCGEventSourceStateCombinedSessionState,  # ty:ignore[unresolved-import]
    kCGEventSourceStateHIDSystemState,  # ty:ignore[unresolved-import]
    kCGEventSuppressionStateSuppressionInterval,  # ty:ignore[unresolved-import]
    kCGHIDEventTap,  # ty:ignore[unresolved-import]
    kCGMouseButtonLeft,  # ty:ignore[unresolved-import]
    kCGMouseEventDeltaX,  # ty:ignore[unresolved-import]
    kCGMouseEventDeltaY,  # ty:ignore[unresolved-import]
)

# ---------------------------------------------------------------------------
# IOKit HID system plumbing (IOKit/hidsystem/IOHIDLib.h, IOKit/IOLLEvent.h)
# ---------------------------------------------------------------------------

# IOServiceOpen connect types (IOKit/hidsystem/IOHIDShared.h)
kIOHIDParamConnectType = 1

# NXEventType values (IOKit/IOLLEvent.h)
NX_LMOUSEDOWN = 1
NX_LMOUSEUP = 2
NX_RMOUSEDOWN = 3
NX_RMOUSEUP = 4
NX_MOUSEMOVED = 5
NX_LMOUSEDRAGGED = 6
NX_RMOUSEDRAGGED = 7
NX_OMOUSEDOWN = 25
NX_OMOUSEUP = 26
NX_OMOUSEDRAGGED = 27

# IOHIDPostEvent option bits (IOKit/hidsystem/IOHIDLib.h)
kIOHIDSetGlobalEventFlags = 0x00000001
kIOHIDSetCursorPosition = 0x00000002
kIOHIDSetRelativeCursorPosition = 0x00000004
kIOHIDPostHIDManagerEvent = 0x00000008

kNXEventDataVersion = 2

kIOReturnSuccess = 0
# Codes worth naming in the report; anything else is printed raw.
_IORETURN_NAMES = {
    0x00000000: "kIOReturnSuccess",
    0xE00002C1: "kIOReturnNotPrivileged",
    0xE00002C7: "kIOReturnUnsupported",
    0xE00002BC: "kIOReturnError",
    0xE00002C2: "kIOReturnBadArgument",
    0xE00002CD: "kIOReturnNotPermitted",
}


class IOGPoint(ctypes.Structure):
    """IOKit's 16-bit screen point (IOKit/graphics/IOGraphicsTypes.h)."""

    _fields_ = [("x", ctypes.c_int16), ("y", ctypes.c_int16)]


class _NXMouseButton(ctypes.Structure):
    """NXEventData's ``mouse`` member, verbatim from IOLLEvent.h."""

    _fields_ = [
        ("subx", ctypes.c_uint8),
        ("suby", ctypes.c_uint8),
        ("eventNum", ctypes.c_int16),
        ("click", ctypes.c_int32),
        ("pressure", ctypes.c_uint8),
        ("buttonNumber", ctypes.c_uint8),
        ("subType", ctypes.c_uint8),
        ("reserved2", ctypes.c_uint8),
        ("reserved3", ctypes.c_int32),
    ]


class _NXMouseMove(ctypes.Structure):
    """Head of NXEventData's ``mouseMove`` member (IOKit/IOLLEvent.h).

    Only the leading dx/dy matter for a relative move; the rest of the union is
    zeroed. The buffer handed to IOHIDPostEvent is deliberately oversized so the
    callee can read the whole union regardless of its exact size.
    """

    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("subx", ctypes.c_uint8),
        ("suby", ctypes.c_uint8),
    ]


_NXEVENTDATA_SIZE = 256


def _ioreturn(code: int) -> str:
    code &= 0xFFFFFFFF
    return _IORETURN_NAMES.get(code, f"0x{code:08X}")


def _load_iokit():
    iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")

    iokit.IOServiceMatching.restype = ctypes.c_void_p
    iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]

    iokit.IOServiceGetMatchingService.restype = ctypes.c_uint32
    iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint32, ctypes.c_void_p]

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

    # IOHIDPostEvent(io_connect_t, UInt32 type, IOGPoint loc, const NXEventData *,
    #                UInt32 version, IOOptionBits flags, IOOptionBits options)
    iokit.IOHIDPostEvent.restype = ctypes.c_int32
    iokit.IOHIDPostEvent.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        IOGPoint,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    return iokit


def _mach_task_self() -> int:
    """``mach_task_self()`` is a macro over the ``mach_task_self_`` global."""
    libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    return ctypes.c_uint32.in_dll(libsystem, "mach_task_self_").value


class HIDInjector:
    """Thin ctypes wrapper over the IOHIDSystem parameter connection."""

    def __init__(self):
        self._iokit = _load_iokit()
        self._service = 0
        self._connect = 0
        self.open_detail = "not attempted"

    def open(self) -> bool:
        matching = self._iokit.IOServiceMatching(b"IOHIDSystem")
        if not matching:
            self.open_detail = "IOServiceMatching(IOHIDSystem) returned NULL"
            return False
        # IOServiceGetMatchingService consumes the matching dictionary.
        self._service = self._iokit.IOServiceGetMatchingService(0, matching)
        if not self._service:
            self.open_detail = "IOHIDSystem service not found"
            return False

        connect = ctypes.c_uint32(0)
        rc = self._iokit.IOServiceOpen(
            self._service,
            _mach_task_self(),
            kIOHIDParamConnectType,
            ctypes.byref(connect),
        )
        if rc != kIOReturnSuccess or not connect.value:
            self.open_detail = f"IOServiceOpen -> {_ioreturn(rc)}"
            return False
        self._connect = connect.value
        self.open_detail = f"connected (io_connect_t={self._connect})"
        return True

    def post_button(
        self, button_number: int, is_down: bool, *, click_count: int
    ) -> int:
        """Post a button event through the HID system.

        With the button state known to the IOHIDSystem, a following NX_MOUSEMOVED
        should come out as a …MouseDragged event, the way hardware behaves - and
        crucially without any absolute position, so a grabbed cursor stays put.
        """
        types = {
            0: (NX_LMOUSEDOWN, NX_LMOUSEUP),
            1: (NX_RMOUSEDOWN, NX_RMOUSEUP),
        }.get(button_number, (NX_OMOUSEDOWN, NX_OMOUSEUP))
        buf = (ctypes.c_uint8 * _NXEVENTDATA_SIZE)()
        mouse = ctypes.cast(buf, ctypes.POINTER(_NXMouseButton)).contents
        mouse.click = int(click_count)
        mouse.pressure = 255 if is_down else 0
        mouse.buttonNumber = int(button_number)
        return self._iokit.IOHIDPostEvent(
            self._connect,
            types[0] if is_down else types[1],
            IOGPoint(0, 0),
            ctypes.byref(buf),
            kNXEventDataVersion,
            0,
            0,
        )

    def post_relative(
        self, dx: int, dy: int, *, flags: int = 0, event_type: int = NX_MOUSEMOVED
    ) -> int:
        """Post one relative motion event. Returns IOReturn.

        ``event_type`` must be the dragged variant while a button is held: the
        HID system posts the type we ask for, it does not derive it from the
        button state.
        """
        buf = (ctypes.c_uint8 * _NXEVENTDATA_SIZE)()
        move = ctypes.cast(buf, ctypes.POINTER(_NXMouseMove)).contents
        move.dx = int(dx)
        move.dy = int(dy)
        return self._iokit.IOHIDPostEvent(
            self._connect,
            event_type,
            IOGPoint(0, 0),
            ctypes.byref(buf),
            kNXEventDataVersion,
            flags,
            kIOHIDSetRelativeCursorPosition,
        )

    def close(self) -> None:
        if self._connect:
            self._iokit.IOServiceClose(self._connect)
            self._connect = 0
        if self._service:
            self._iokit.IOObjectRelease(self._service)
            self._service = 0


# ---------------------------------------------------------------------------
# Position sources
# ---------------------------------------------------------------------------


def event_system_position() -> tuple[float, float]:
    """Authoritative cursor location per the event system (top-left origin)."""
    loc = CGEventGetLocation(CGEventCreate(None))
    return float(loc.x), float(loc.y)


def nsevent_position() -> tuple[float, float]:
    """What pynput reads: NSEvent.mouseLocation(), flipped the same way."""
    pos = NSEvent.mouseLocation()
    return float(pos.x), float(CGDisplayPixelsHigh(0) - pos.y)


def _fmt_pos(pos: tuple[float, float]) -> str:
    return f"({pos[0]:.1f}, {pos[1]:.1f})"


def post_cgevent_relative(dx: int, dy: int) -> tuple[float, float]:
    """The current product behaviour: absolute position + stamped deltas."""
    x, y = event_system_position()
    nx, ny = x + dx, y + dy
    event = CGEventCreateMouseEvent(
        None, kCGEventMouseMoved, (nx, ny), kCGMouseButtonLeft
    )
    CGEventSetIntegerValueField(event, kCGMouseEventDeltaX, int(dx))
    CGEventSetIntegerValueField(event, kCGMouseEventDeltaY, int(dy))
    CGEventPost(kCGHIDEventTap, event)
    return nx, ny


def _time_to_first_movement(hid: "HIDInjector", *, delta: int = 10) -> float:
    """Milliseconds until HID injection starts moving the cursor again.

    A warp (or any posted CGEvent) suppresses *local hardware* events for
    ``CGSetLocalEventsSuppressionInterval`` seconds - 0.25 s by default - and our
    HID injection is local hardware input, so it suppresses itself.
    """
    start = event_system_position()
    t0 = time.perf_counter()
    deadline = t0 + 2.0
    while time.perf_counter() < deadline:
        hid.post_relative(delta, 0)
        if event_system_position() != start:
            return (time.perf_counter() - t0) * 1000.0
        time.sleep(0.004)
    return float("inf")


def check_suppression(hid: HIDInjector) -> None:
    """Measure the suppression window, then each candidate remedy."""
    print("== 6. Local events suppression after a warp ==")
    here = event_system_position()

    def warp(n: int) -> None:
        for _ in range(n):
            CGWarpMouseCursorPosition((here[0], here[1]))

    for warps in (1, 20):
        warp(warps)
        ms = _time_to_first_movement(hid)
        shown = "never within 2s" if ms == float("inf") else f"{ms:.1f} ms"
        print(f"  baseline, {warps:>2} warp(s): frozen for {shown}")
        time.sleep(0.4)

    # (a) the deprecated global setter
    try:
        CGSetLocalEventsSuppressionInterval(0.0)
        warp(20)
        ms = _time_to_first_movement(hid)
        shown = "never within 2s" if ms == float("inf") else f"{ms:.1f} ms"
        print(f"  (a) CGSetLocalEventsSuppressionInterval(0.0), 20 warps: {shown}")
    except Exception as e:
        print(f"  (a) CGSetLocalEventsSuppressionInterval unavailable: {e}")
    time.sleep(0.4)

    # (b) modern per-source filter during the suppression state
    try:
        src = CGEventSourceCreate(kCGEventSourceStateHIDSystemState)
        CGEventSourceSetLocalEventsFilterDuringSuppressionState(
            src,
            kCGEventFilterMaskPermitAllEvents,
            kCGEventSuppressionStateSuppressionInterval,
        )
        CGSetLocalEventsSuppressionInterval(0.25)  # restore, isolate (b)
        warp(20)
        ms = _time_to_first_movement(hid)
        shown = "never within 2s" if ms == float("inf") else f"{ms:.1f} ms"
        print(f"  (b) filter permit-all during suppression, 20 warps: {shown}")
    except Exception as e:
        print(f"  (b) event-source filter unavailable: {e}")
    time.sleep(0.4)

    # (c) re-associate right after the warp (SDL / Chromium trick)
    try:
        CGSetLocalEventsSuppressionInterval(0.25)
        warp(20)
        CGAssociateMouseAndMouseCursorPosition(True)
        ms = _time_to_first_movement(hid)
        shown = "never within 2s" if ms == float("inf") else f"{ms:.1f} ms"
        print(f"  (c) CGAssociateMouseAndMouseCursorPosition(True) after warp: {shown}")
    except Exception as e:
        print(f"  (c) re-associate unavailable: {e}")

    # Also: does merely POSTING a CGEvent (what an injected click does) suppress?
    try:
        CGSetLocalEventsSuppressionInterval(0.25)
        post_cgevent_relative(0, 0)
        ms = _time_to_first_movement(hid)
        shown = "never within 2s" if ms == float("inf") else f"{ms:.1f} ms"
        print(f"  posted CGEvent (no warp) freezes HID motion for: {shown}")
    except Exception as e:
        print(f"  CGEvent post check failed: {e}")


def check_linearity(hid: HIDInjector) -> None:
    """Is a single large HID delta applied 1:1, or does acceleration scale it?

    Decides whether absolute placement could be done as HID motion instead of a
    warp (remedy (d)), which would need no suppression workaround at all.
    """
    print("== 7. Single-event linearity (large deltas) ==")
    for delta in (10, 100, 500, 1000):
        start = event_system_position()
        hid.post_relative(delta, 0)
        time.sleep(0.05)
        moved = event_system_position()[0] - start[0]
        print(f"  {delta:>5} units -> {moved:+.1f}px ({moved / delta:.2f} px/unit)")
        # Come back so we don't run off the display.
        hid.post_relative(-delta, 0)
        time.sleep(0.05)


_TAP_TYPE_NAMES = {
    1: "LeftMouseDown",
    2: "LeftMouseUp",
    3: "RightMouseDown",
    4: "RightMouseUp",
    5: "MouseMoved",
    6: "LeftMouseDragged",
    7: "RightMouseDragged",
    25: "OtherMouseDown",
    26: "OtherMouseUp",
    27: "OtherMouseDragged",
}


def _observe(seconds: float, produce) -> list[str]:
    """Run ``produce`` while a listen-only tap records the event types produced.

    This is how we tell whether the IOHIDSystem turns an NX_MOUSEMOVED posted
    while a button is held into a …MouseDragged, the way hardware does. Needs
    Accessibility for this process; returns [] if the tap can't be created.
    """
    seen: list[str] = []
    mask = 0
    for t in _TAP_TYPE_NAMES:
        mask |= 1 << t

    def callback(proxy, etype, event, refcon):
        seen.append(_TAP_TYPE_NAMES.get(etype, str(etype)))
        return event

    tap = CGEventTapCreate(
        kCGHIDEventTap,
        kCGTailAppendEventTap,
        kCGEventTapOptionListenOnly,
        mask,
        callback,
        None,
    )
    if tap is None:
        print("  (no event tap: grant Accessibility to this terminal to see types)")
        produce()
        return []
    source = CFMachPortCreateRunLoopSource(None, tap, 0)
    CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
    CGEventTapEnable(tap, True)
    produce()
    CFRunLoopRunInMode(kCFRunLoopDefaultMode, seconds, False)
    CGEventTapEnable(tap, False)
    return seen


DRAG_TYPE = NX_MOUSEMOVED


def check_buttons(hid: HIDInjector) -> None:
    """Press via HID, move, release: does the OS treat it as a real drag?"""
    print("== 8. HID buttons and drag ==")
    codes = {}

    def produce():
        codes["down"] = hid.post_button(0, True, click_count=1)
        for _ in range(6):
            hid.post_relative(4, 2, event_type=DRAG_TYPE)
            time.sleep(0.01)
        codes["up"] = hid.post_button(0, False, click_count=1)

    start = event_system_position()
    seen = _observe(0.4, produce)
    end = event_system_position()

    print(
        f"  left down -> {_ioreturn(codes.get('down', -1))}, "
        f"up -> {_ioreturn(codes.get('up', -1))}"
    )
    print(
        f"  cursor while held: {start} -> {end} "
        f"(dx={end[0] - start[0]:+.1f}, dy={end[1] - start[1]:+.1f})"
    )
    if seen:
        order = []
        for name in seen:
            if not order or order[-1][0] != name:
                order.append([name, 1])
            else:
                order[-1][1] += 1
        print("  event types produced: " + ", ".join(f"{n}×{c}" for n, c in order))
        print(
            "  Verdict: "
            + (
                "the HID system emits Dragged - motion can stay on HID while held"
                if any("Dragged" in n for n in seen)
                else "NO Dragged events: a drag needs a CGEvent at the CURRENT position"
            )
        )


def check_game_drag(
    hid: HIDInjector, countdown: int, *, count: int, delta: int
) -> None:
    print(f"== 9. Focused-app burst WITH a button held ({countdown}s to focus) ==")
    for remaining in range(countdown, 0, -1):
        print(f"  {remaining}...", end="\r", flush=True)
        time.sleep(1)
    print("  injecting...        ")
    start = event_system_position()
    hid.post_button(0, True, click_count=1)
    for _ in range(count):
        hid.post_relative(delta, 0)
        time.sleep(0.008)
    time.sleep(0.05)
    end = event_system_position()
    hid.post_button(0, False, click_count=1)
    print(f"  HID with button held: {start} -> {end} (dx={end[0] - start[0]:+.1f}px)")
    print("  Expected under a cursor grab: ~0px, camera still turning.")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _post_cgevent(
    dx: int,
    dy: int,
    *,
    source=None,
    tap=None,
    move_position: bool = True,
) -> None:
    """One CGEvent variant: pick the source, the tap, and whether to command
    movement at all (a delta-only event at the current position is the old
    "pin")."""
    x, y = event_system_position()
    target = (x + dx, y + dy) if move_position else (x, y)
    event = CGEventCreateMouseEvent(
        source, kCGEventMouseMoved, target, kCGMouseButtonLeft
    )
    CGEventSetIntegerValueField(event, kCGMouseEventDeltaX, int(dx))
    CGEventSetIntegerValueField(event, kCGMouseEventDeltaY, int(dy))
    CGEventPost(kCGHIDEventTap if tap is None else tap, event)


def _grab_variants(hid: HIDInjector, delta: int):
    """The injection variants worth testing against a cursor grab.

    ``kCGAnnotatedSessionEventTap`` is deliberately absent: it does not move the
    cursor even on a plain desktop (measured 0 px of 100 requested), so a 0 here
    would mean "the event went nowhere", not "the grab was honoured".
    """
    hid_source = CGEventSourceCreate(kCGEventSourceStateHIDSystemState)
    session_source = CGEventSourceCreate(kCGEventSourceStateCombinedSessionState)
    return [
        ("IOHIDPostEvent (control)", lambda: hid.post_relative(delta, 0)),
        ("CGEvent src=NULL tap=HID", lambda: _post_cgevent(delta, 0)),
        (
            "CGEvent src=HIDSystemState",
            lambda: _post_cgevent(delta, 0, source=hid_source),
        ),
        (
            "CGEvent src=CombinedSession",
            lambda: _post_cgevent(delta, 0, source=session_source),
        ),
        (
            "CGEvent tap=Session",
            lambda: _post_cgevent(delta, 0, tap=kCGSessionEventTap),
        ),
        (
            "CGEvent delta-only (old pin)",
            lambda: _post_cgevent(delta, 0, move_position=False),
        ),
        (
            "CGWarpMouseCursorPosition",
            lambda: CGWarpMouseCursorPosition(
                (event_system_position()[0] + delta, event_system_position()[1])
            ),
        ),
    ]


def check_association_in_game(
    hid: HIDInjector, countdown: int, *, count: int = 10, delta: int = 10
) -> None:
    """Run the variant matrix against a REAL grab - the only valid environment.

    Reproducing the grab locally does not work: calling
    ``CGAssociateMouseAndMouseCursorPosition(False)`` from a background process
    leaves even ``IOHIDPostEvent`` moving the cursor, i.e. the control fails and
    the whole comparison is void. A game in the foreground is the real thing.

    Read the table as: 0 px = the variant honours the grab (what the HID path
    does today), anything else = it bypasses it, which is the drift that walks
    the pointer out of the game window.
    """
    print(
        f"== 10. Injection variants under a REAL grab - focus the game ({countdown}s) =="
    )
    for remaining in range(countdown, 0, -1):
        print(f"  {remaining}...", end="\r", flush=True)
        time.sleep(1)
    print("  injecting...        ")

    for name, inject in _grab_variants(hid, delta):
        start = event_system_position()
        for _ in range(count):
            inject()
            time.sleep(0.008)
        time.sleep(0.05)
        end = event_system_position()
        moved = end[0] - start[0]
        verdict = "HONOURS the grab" if abs(moved) < 2 else "bypasses it"
        print(f"  {name:30} cursor moved {moved:+7.1f}px   {verdict}")
        if abs(moved) >= 1:
            # Put it back, or the next variant starts from a drifted position
            # (and the game may lose focus).
            CGWarpMouseCursorPosition(start)
            time.sleep(0.1)
    print(
        f"  (requested {delta * count:+d}px per variant; camera should turn throughout)"
    )


def check_association(hid: HIDInjector, *, count: int = 20, delta: int = 10) -> None:
    """Does ANY CGEvent variant honour a cursor grab, the way the HID path does?

    The grab is reproduced here rather than borrowed from a game: disassociating
    the mouse from the cursor is exactly what an app doing a cursor lock does,
    and exactly what our own server listener does when it pins the local cursor.
    A variant that leaves the cursor at 0 px would make the CGEvent fallback
    equivalent to the HID path.
    """
    print("== 10. CGEvent variants under a reproduced cursor grab ==")

    hid_source = CGEventSourceCreate(kCGEventSourceStateHIDSystemState)
    session_source = CGEventSourceCreate(kCGEventSourceStateCombinedSessionState)
    variants = [
        ("IOHIDPostEvent (control)", lambda: hid.post_relative(delta, 0)),
        ("CGEvent src=NULL tap=HID", lambda: _post_cgevent(delta, 0)),
        (
            "CGEvent src=HIDSystemState",
            lambda: _post_cgevent(delta, 0, source=hid_source),
        ),
        (
            "CGEvent src=CombinedSession",
            lambda: _post_cgevent(delta, 0, source=session_source),
        ),
        (
            "CGEvent tap=Session",
            lambda: _post_cgevent(delta, 0, tap=kCGSessionEventTap),
        ),
        (
            "CGEvent tap=AnnotatedSession",
            lambda: _post_cgevent(delta, 0, tap=kCGAnnotatedSessionEventTap),
        ),
        (
            "CGEvent delta-only (the old pin)",
            lambda: _post_cgevent(delta, 0, move_position=False),
        ),
        (
            "CGWarpMouseCursorPosition",
            lambda: CGWarpMouseCursorPosition(
                (event_system_position()[0] + delta, event_system_position()[1])
            ),
        ),
    ]

    # A cursor left disassociated freezes the physical mouse, so the restore is
    # registered before anything is disassociated and repeated in `finally`.
    atexit.register(CGAssociateMouseAndMouseCursorPosition, True)
    try:
        for name, inject in variants:
            start = event_system_position()
            CGAssociateMouseAndMouseCursorPosition(False)
            try:
                for _ in range(count):
                    inject()
                    time.sleep(0.008)
                time.sleep(0.05)
                end = event_system_position()
            finally:
                CGAssociateMouseAndMouseCursorPosition(True)
            moved = end[0] - start[0]
            verdict = "HONOURS the grab" if abs(moved) < 2 else "bypasses it"
            print(f"  {name:34} cursor moved {moved:+7.1f}px   {verdict}")
            # Put the cursor back so the next variant starts from the same place.
            if abs(moved) >= 1:
                CGWarpMouseCursorPosition(start)
            time.sleep(0.1)
    finally:
        CGAssociateMouseAndMouseCursorPosition(True)
    print(f"  (requested {delta * count:+d}px per variant)")


def check_edge_readback(hid: HIDInjector, *, count: int = 30, delta: int = 20) -> None:
    """At the screen edge, does the READ-BACK position stay bounded?

    The historical macOS problem was not the cursor - the OS keeps that on
    screen - but the *value*: pushing past the edge grew pynput's
    ``controller.position`` without end. Our read has since moved to
    CGEventGetLocation, so this re-measures all three injection paths against
    both position sources.
    """
    print("== 11. Read-back at the screen edge ==")
    bounds = CGDisplayBounds(CGMainDisplayID())
    left = bounds.origin.x
    print(f"  main display starts at x={left}")

    controller = _PynputController()

    def run(name: str, inject) -> None:
        # Park the cursor on the left edge first.
        CGWarpMouseCursorPosition((left, bounds.origin.y + bounds.size.height / 2))
        time.sleep(0.05)
        first_es = event_system_position()
        for i in range(count):
            inject()
            time.sleep(0.008)
            if i in (0, count // 2, count - 1):
                es, ns = event_system_position(), nsevent_position()
                print(
                    f"    {name:22} evt {i:>2}: CGEventGetLocation x={es[0]:>10.1f}  "
                    f"NSEvent x={ns[0]:>10.1f}"
                )
        es = event_system_position()
        grew = es[0] < left - 2
        print(
            f"    {name:22} after {count} pushes: x={es[0]:.1f} "
            f"(started {first_es[0]:.1f}) -> "
            + ("VALUE GROWS past the edge" if grew else "stays clamped")
        )

    run("HID", lambda: hid.post_relative(-delta, 0))
    run("CGEvent (fallback)", lambda: _post_cgevent(-delta, 0))
    run("pynput move (historic)", lambda: controller.move(-delta, 0))


def check_privilege() -> None:
    print("== 5. Privileges ==")
    euid = os.geteuid()
    try:
        console_uid = os.stat("/dev/console").st_uid
        console_user = pwd.getpwuid(console_uid).pw_name
    except Exception as e:  # pragma: no cover - diagnostic
        print(f"  /dev/console: unreadable ({e})")
        return
    me = pwd.getpwuid(euid).pw_name
    ok = "OK" if euid == console_uid else "MISMATCH -> expect kIOReturnNotPrivileged"
    print(
        f"  euid={euid} ({me})  /dev/console owner={console_uid} ({console_user})  [{ok}]"
    )


def check_connection(hid: HIDInjector) -> bool:
    print("== 1. IOHIDSystem connection ==")
    ok = hid.open()
    print(f"  {hid.open_detail}")
    return ok


def check_desktop_burst(hid: HIDInjector, *, count: int, delta: int) -> None:
    print("== 2. Relative burst (HID) ==")
    for axis, (dx, dy) in (("x", (delta, 0)), ("y", (0, delta))):
        start = event_system_position()
        codes = set()
        for _ in range(count):
            codes.add(hid.post_relative(dx, dy))
            time.sleep(0.008)
        time.sleep(0.05)
        end = event_system_position()
        moved = (end[0] - start[0], end[1] - start[1])
        requested = delta * count
        seen = moved[0] if axis == "x" else moved[1]
        ratio = (seen / requested) if requested else 0.0
        rcs = ", ".join(sorted(_ioreturn(c) for c in codes))
        print(
            f"  {axis}: requested {requested:+d} units -> cursor moved {seen:+.1f}px "
            f"({ratio:.2f} px/unit)  IOReturn: {rcs}"
        )
        print(f"     {start} -> {end}")


def check_cgevent_burst(*, count: int, delta: int) -> None:
    """Same burst through the current product path, at a realistic event rate.

    The CGEvent path is read-modify-write over a cursor position that only
    catches up ~17 ms later, so at a 125 Hz stream it should lose movement.
    """
    print("== 2b. Relative burst (CGEvent, current behaviour) ==")
    for label, gap in (("125 Hz (8ms)", 0.008), ("slow (30ms)", 0.030)):
        start = event_system_position()
        for _ in range(count):
            post_cgevent_relative(delta, 0)
            time.sleep(gap)
        time.sleep(0.05)
        end = event_system_position()
        requested = delta * count
        seen = end[0] - start[0]
        print(
            f"  {label}: requested {requested:+d}px -> cursor moved {seen:+.1f}px "
            f"({seen / requested:.2f} of it)"
        )


def check_position_lag(hid: HIDInjector, *, use_hid: bool, delta: int = 40) -> None:
    print(f"== 4. Position source lag ({'HID' if use_hid else 'CGEvent'} injection) ==")
    before_es = event_system_position()
    before_ns = nsevent_position()
    t0 = time.perf_counter()
    if use_hid:
        hid.post_relative(delta, 0)
    else:
        post_cgevent_relative(delta, 0)

    seen = {"event_system": None, "nsevent": None}
    deadline = t0 + 0.5
    while time.perf_counter() < deadline and not all(seen.values()):
        now = (time.perf_counter() - t0) * 1000.0
        if seen["event_system"] is None and event_system_position()[0] != before_es[0]:
            seen["event_system"] = now
        if seen["nsevent"] is None and nsevent_position()[0] != before_ns[0]:
            seen["nsevent"] = now
    for name, ms in seen.items():
        print(f"  {name}: {'never within 500ms' if ms is None else f'{ms:.1f} ms'}")
    print(
        f"  final: event_system={event_system_position()} nsevent={nsevent_position()}"
    )


def check_game(hid: HIDInjector, countdown: int, *, count: int, delta: int) -> None:
    print(f"== 3. Focused-app burst - focus the game now ({countdown}s) ==")
    for remaining in range(countdown, 0, -1):
        print(f"  {remaining}...", end="\r", flush=True)
        time.sleep(1)
    print("  injecting...        ")

    start = event_system_position()
    for _ in range(count):
        hid.post_relative(delta, 0)
        time.sleep(0.008)
    time.sleep(0.05)
    mid = event_system_position()
    print(f"  HID     : {start} -> {mid}  (dx={mid[0] - start[0]:+.1f}px)")

    for _ in range(count):
        post_cgevent_relative(delta, 0)
        time.sleep(0.008)
    time.sleep(0.05)
    end = event_system_position()
    print(f"  CGEvent : {mid} -> {end}  (dx={end[0] - mid[0]:+.1f}px)")
    print(
        "  Interpretation: if the app dissociated the cursor, HID should move it ~0px\n"
        "  while the camera still turns, and CGEvent should move it (the bypass we\n"
        "  are trying to get rid of)."
    )


def trace_focused_app_motion(
    countdown: int,
    *,
    seconds: float,
    fixed_threshold: float,
) -> None:
    """Passively observe physical mouse events while a game is focused.

    This is the detection probe for apps that called
    ``CGAssociateMouseAndMouseCursorPosition(False)`` themselves: during a real
    grab, hardware events keep carrying non-zero dx/dy while the public cursor
    position stays fixed. No movement is injected here; the user supplies the
    physical mouse motion.
    """
    print(
        f"== 12. Passive focused-app trace - focus the game ({countdown}s), "
        f"then move the mouse for {seconds:.1f}s =="
    )
    for remaining in range(countdown, 0, -1):
        print(f"  {remaining}...", end="\r", flush=True)
        time.sleep(1)
    print("  observing...        ")

    start_es = event_system_position()
    start_ns = nsevent_position()
    print(
        f"  start: CGEventGetLocation={_fmt_pos(start_es)}  "
        f"NSEvent={_fmt_pos(start_ns)}"
    )

    mask = 0
    for event_type in _TAP_TYPE_NAMES:
        mask |= 1 << event_type

    events = []
    samples = []
    t0 = time.perf_counter()

    def snapshot() -> tuple[float, tuple[float, float], tuple[float, float]]:
        return time.perf_counter() - t0, event_system_position(), nsevent_position()

    def callback(proxy, etype, event, refcon):
        loc = CGEventGetLocation(event)
        dx = int(CGEventGetIntegerValueField(event, kCGMouseEventDeltaX))
        dy = int(CGEventGetIntegerValueField(event, kCGMouseEventDeltaY))
        now, es, ns = snapshot()
        events.append(
            (
                now,
                _TAP_TYPE_NAMES.get(etype, str(etype)),
                dx,
                dy,
                (float(loc.x), float(loc.y)),
                es,
                ns,
            )
        )
        return event

    tap = CGEventTapCreate(
        kCGHIDEventTap,
        kCGTailAppendEventTap,
        kCGEventTapOptionListenOnly,
        mask,
        callback,
        None,
    )
    if tap is None:
        print(
            "  no event tap: grant Accessibility to this terminal to read dx/dy; "
            "falling back to position polling only"
        )
    else:
        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)

    try:
        next_line = 0.5
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            remaining = max(0.0, deadline - time.perf_counter())
            step = min(0.05, remaining)
            if tap is None:
                time.sleep(step)
            else:
                CFRunLoopRunInMode(kCFRunLoopDefaultMode, step, False)

            now, es, ns = snapshot()
            samples.append((now, es, ns))
            if now >= next_line:
                recent = [e for e in events if now - e[0] <= 0.5]
                recent_dx = sum(e[2] for e in recent)
                recent_dy = sum(e[3] for e in recent)
                print(
                    f"  t={now:4.1f}s  CG={_fmt_pos(es)}  NS={_fmt_pos(ns)}  "
                    f"events={len(recent):3d}/0.5s  "
                    f"delta=({recent_dx:+4d},{recent_dy:+4d})"
                )
                next_line += 0.5
    finally:
        if tap is not None:
            CGEventTapEnable(tap, False)

    all_es = [start_es] + [s[1] for s in samples] + [e[5] for e in events]
    all_ns = [start_ns] + [s[2] for s in samples] + [e[6] for e in events]
    all_event_locs = [e[4] for e in events]

    def spread(points: list[tuple[float, float]]) -> tuple[float, float]:
        if not points:
            return 0.0, 0.0
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return max(xs) - min(xs), max(ys) - min(ys)

    es_spread = spread(all_es)
    ns_spread = spread(all_ns)
    event_loc_spread = spread(all_event_locs)
    nonzero_delta_events = [e for e in events if e[2] or e[3]]
    total_dx = sum(e[2] for e in nonzero_delta_events)
    total_dy = sum(e[3] for e in nonzero_delta_events)
    max_abs_delta = (
        max(max(abs(e[2]), abs(e[3])) for e in nonzero_delta_events)
        if nonzero_delta_events
        else 0
    )
    position_fixed = (
        max(es_spread) <= fixed_threshold
        and max(ns_spread) <= fixed_threshold
        and (not all_event_locs or max(event_loc_spread) <= fixed_threshold)
    )
    saw_deltas = len(nonzero_delta_events) > 0

    print("  summary:")
    print(
        f"    events={len(events)}  nonzero-delta={len(nonzero_delta_events)}  "
        f"sum_delta=({total_dx:+d},{total_dy:+d})  max_abs_delta={max_abs_delta}"
    )
    print(
        f"    spread: CGEventGetLocation=({es_spread[0]:.1f},{es_spread[1]:.1f})px  "
        f"NSEvent=({ns_spread[0]:.1f},{ns_spread[1]:.1f})px  "
        f"event.location=({event_loc_spread[0]:.1f},{event_loc_spread[1]:.1f})px"
    )
    print(
        "    verdict: "
        + (
            "position is fixed while deltas continue -> consistent with a cursor grab"
            if position_fixed and saw_deltas
            else "position moves with the deltas -> no cursor grab observed"
            if saw_deltas
            else (
                "no dx/dy observed -> move the mouse during the trace, "
                "or grant Accessibility"
            )
        )
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--game",
        type=int,
        metavar="SECONDS",
        help="countdown before the focused-app burst, to switch to the game",
    )
    ap.add_argument(
        "--game-drag",
        type=int,
        metavar="SECONDS",
        help="like --game, but with a mouse button held down through the burst",
    )
    ap.add_argument(
        "--suppression",
        action="store_true",
        help="measure the post-warp local-events suppression and each remedy",
    )
    ap.add_argument(
        "--buttons",
        action="store_true",
        help="HID button + drag behaviour on the desktop",
    )
    ap.add_argument(
        "--assoc",
        action="store_true",
        help="do any CGEvent variants honour a cursor grab? (reproduces the grab)",
    )
    ap.add_argument(
        "--assoc-game",
        type=int,
        metavar="SECONDS",
        help="the variant matrix against a REAL grab: countdown, then focus the game",
    )
    ap.add_argument(
        "--edge",
        action="store_true",
        help="does the read-back position stay bounded at the screen edge?",
    )
    ap.add_argument(
        "--trace-game",
        type=int,
        metavar="SECONDS",
        help=(
            "passively trace physical dx/dy and cursor positions after a countdown; "
            "use this while entering/focusing a game"
        ),
    )
    ap.add_argument(
        "--trace-seconds",
        type=float,
        default=8.0,
        help="duration for --trace-game observation",
    )
    ap.add_argument(
        "--fixed-threshold",
        type=float,
        default=2.0,
        help="max cursor-position spread, in px, still treated as fixed",
    )
    ap.add_argument("--count", type=int, default=40, help="events per burst")
    ap.add_argument("--delta", type=int, default=10, help="delta units per event")
    args = ap.parse_args()

    print(
        f"macOS probe - python {sys.version.split()[0]}, darwin {os.uname().release}\n"
    )
    check_privilege()
    print()

    if args.trace_game is not None:
        trace_focused_app_motion(
            args.trace_game,
            seconds=args.trace_seconds,
            fixed_threshold=args.fixed_threshold,
        )
        return 0

    hid = HIDInjector()
    try:
        if not check_connection(hid):
            print(
                "\nHID path unavailable: stopping here (nothing else can be measured)."
            )
            return 1
        print()
        if args.game:
            check_game(hid, args.game, count=args.count, delta=args.delta)
            return 0
        if args.game_drag:
            check_game_drag(hid, args.game_drag, count=args.count, delta=args.delta)
            return 0
        if args.suppression:
            check_suppression(hid)
            print()
            check_linearity(hid)
            return 0
        if args.buttons:
            check_buttons(hid)
            return 0
        if args.assoc:
            check_association(hid)
            return 0
        if args.assoc_game:
            check_association_in_game(hid, args.assoc_game)
            return 0
        if args.edge:
            check_edge_readback(hid)
            return 0
        check_desktop_burst(hid, count=args.count, delta=args.delta)
        print()
        check_cgevent_burst(count=args.count, delta=args.delta)
        print()
        check_position_lag(hid, use_hid=True)
        print()
        check_position_lag(hid, use_hid=False)
    finally:
        hid.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
