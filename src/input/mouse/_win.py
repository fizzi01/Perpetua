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
import threading
from ctypes import wintypes
from typing import Optional

from event import (
    BusEventType,
    MouseEvent,
    ActiveScreenChangedEvent,
    ClientDisconnectedEvent,
)

from . import _base


# ShowCursor is per-thread and only honoured when the calling thread owns the
# foreground window, so it can't hide the cursor system-wide from a background
# service. SetSystemCursor swaps every system cursor for a blank bitmap and
# takes effect immediately for every app; SPI_SETCURSORS reloads the user's
# configured cursors from the registry to restore them.
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
    """Reload the user's configured system cursors from the registry."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SystemParametersInfoW(_SPI_SETCURSORS, 0, None, 0)
    except Exception:
        pass


if sys.platform == "win32":
    # If we crash before disable_capture runs the user is stuck with a blank
    # cursor until reboot.
    atexit.register(_restore_system_cursors)


def _hide_system_cursors() -> None:
    """Replace every system cursor with a fully transparent 32x32 bitmap."""
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    width, height = 32, 32
    mask_size = (width // 8) * height
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
            continue


_WM_DESTROY = 0x0002
_WM_CLOSE = 0x0010
_WM_INPUT = 0x00FF

_RID_INPUT = 0x10000003
_RIM_TYPEMOUSE = 0
_RIDEV_INPUTSINK = 0x00000100

_HID_USAGE_PAGE_GENERIC = 0x01
_HID_USAGE_GENERIC_MOUSE = 0x02

_MOUSE_MOVE_ABSOLUTE = 0x01

# HWND_MESSAGE is a magic sentinel, not a real HWND. It must be passed as a
# plain int so ctypes routes it through HWND (== c_void_p) cleanly - wrapping
# it in c_void_p drops the sign-extended high bits on some builds and turns
# the message-only window into a top-level one.
_HWND_MESSAGE = -3


class _RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", ctypes.c_ushort),
        ("usUsage", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("hwndTarget", wintypes.HWND),
    ]


class _RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", ctypes.c_ulong),
        ("dwSize", ctypes.c_ulong),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _RAWMOUSE_BUTTONS(ctypes.Structure):
    _fields_ = [
        ("usButtonFlags", ctypes.c_ushort),
        ("usButtonData", ctypes.c_ushort),
    ]


class _RAWMOUSE_UNION(ctypes.Union):
    _fields_ = [
        ("ulButtons", ctypes.c_ulong),
        ("buttons", _RAWMOUSE_BUTTONS),
    ]


class _RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", ctypes.c_ushort),
        ("u", _RAWMOUSE_UNION),
        ("ulRawButtons", ctypes.c_ulong),
        ("lLastX", ctypes.c_long),
        ("lLastY", ctypes.c_long),
        ("ulExtraInformation", ctypes.c_ulong),
    ]


class _RAWINPUT(ctypes.Structure):
    # The real RAWINPUT.data is a union of mouse/keyboard/hid; we only
    # register for mouse so the mouse field is enough.
    _fields_ = [
        ("header", _RAWINPUTHEADER),
        ("mouse", _RAWMOUSE),
    ]


_RAWINPUTHEADER_SIZE = ctypes.sizeof(_RAWINPUTHEADER)
_RAWINPUT_SIZE = ctypes.sizeof(_RAWINPUT)

# Thread scheduling: bump the message-pump thread above normal so WM_INPUT
# dispatch isn't preempted by background work under load.
_THREAD_PRIORITY_ABOVE_NORMAL = 1


_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# Relative-motion injection (SendInput). MOUSEEVENTF_MOVE without
# MOUSEEVENTF_ABSOLUTE makes dx/dy relative deltas, which is what games
# reading DirectInput/relative movement consume — unlike pynput's absolute
# cursor warp.
_INPUT_MOUSE = 0
_MOUSEEVENTF_MOVE = 0x0001

_ULONG_PTR = (
    ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else wintypes.DWORD
)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    ]


def _configure_win32_signatures() -> None:
    """Pin argtypes/restype on user32 entry points used by the capture path.

    CreateWindowExW and DefWindowProcW return pointer-sized values; without
    an explicit restype ctypes truncates them to 32-bit on 64-bit Windows.
    """
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.SendInput.argtypes = [
        wintypes.UINT,
        ctypes.POINTER(_INPUT),
        ctypes.c_int,
    ]
    user32.SendInput.restype = wintypes.UINT

    user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM

    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL

    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND

    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL

    user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t

    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.GetMessageW.restype = ctypes.c_int

    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL

    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t

    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL

    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostQuitMessage.restype = None

    user32.RegisterRawInputDevices.argtypes = [
        ctypes.POINTER(_RAWINPUTDEVICE),
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.RegisterRawInputDevices.restype = wintypes.BOOL

    user32.GetRawInputData.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    ]
    user32.GetRawInputData.restype = wintypes.UINT

    user32.ClipCursor.argtypes = [ctypes.POINTER(wintypes.RECT)]
    user32.ClipCursor.restype = wintypes.BOOL

    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = wintypes.DWORD


_configure_win32_signatures()


class _RawMouseCapture:
    """Owns the HWND_MESSAGE window, raw-input registration, ClipCursor pin
    and the dedicated message-pump thread.

    The pump thread creates the window, registers raw input and runs the
    blocking GetMessageW loop; start() waits on a ready event so the caller
    is guaranteed the window is live before it returns.
    """

    # Class registration is process-global, so reuse across instances would
    # collide if a queued message still referenced the old class.
    _instance_seq = 0
    _instance_seq_lock = threading.Lock()

    def __init__(self, loop: asyncio.AbstractEventLoop, on_delta, logger):
        self._loop = loop
        self._on_delta = on_delta
        self._logger = logger
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        # Pre-bind the C callables used per WM_INPUT to dodge attribute
        # lookups on the hot path.
        self._GetRawInputData = self._user32.GetRawInputData
        self._DefWindowProcW = self._user32.DefWindowProcW
        self._call_soon = loop.call_soon_threadsafe
        # Reusable RAWINPUT buffer + size cell: GetRawInputData fills it in
        # place per call, so allocating once per capture session is enough.
        self._raw_buf = _RAWINPUT()
        self._raw_buf_ref = ctypes.byref(self._raw_buf)
        self._raw_size = wintypes.UINT(_RAWINPUT_SIZE)
        self._raw_size_ref = ctypes.byref(self._raw_size)

        with _RawMouseCapture._instance_seq_lock:
            _RawMouseCapture._instance_seq += 1
            seq = _RawMouseCapture._instance_seq
        self._class_name = f"PerpetuaRawInputSink_{seq}"

        self._thread: Optional[threading.Thread] = None
        self._hwnd: int = 0
        self._hinstance = None
        self._ready_evt = threading.Event()
        self._startup_error: Optional[BaseException] = None
        # The WNDPROC callback and WNDCLASS struct must outlive the window
        # class: Windows reads them by pointer and dropping early is a UAF.
        self._wndproc_cb = _WNDPROC(self._wndproc)
        self._wndclass: Optional[_WNDCLASSW] = None

        # Coalescing buffer: a 1 kHz mouse generates ~1000 WM_INPUTs/sec, but
        # we only enqueue one drain task between asyncio ticks.
        self._pending_lock = threading.Lock()
        self._pending_dx = 0
        self._pending_dy = 0
        self._pending_scheduled = False
        self._drain_cb = self._drain_pending

        self._clip_center: tuple[int, int] = (0, 0)

    def start(self, center: tuple[int, int], timeout: float = 2.0) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return True
        self._clip_center = center
        self._ready_evt.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._thread_main,
            name="RawMouseCapture",
            daemon=True,
        )
        self._thread.start()
        if not self._ready_evt.wait(timeout=timeout):
            self._logger.error("Raw input thread did not become ready in time")
            return False
        if self._startup_error is not None:
            self._logger.error(
                f"Raw input thread startup failed ({self._startup_error})"
            )
            return False
        return True

    def stop(self, timeout: float = 2.0) -> None:
        hwnd = self._hwnd
        if hwnd:
            try:
                self._user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
            except Exception as e:
                self._logger.error("PostMessageW(WM_CLOSE) failed", error=str(e))
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                self._logger.warning(
                    "Raw input thread still alive after join", timeout=timeout
                )
        self._thread = None
        # Release from the caller too - if the thread died unexpectedly the
        # cursor would stay pinned at the centre forever.
        try:
            self._user32.ClipCursor(None)
        except Exception:
            pass

    def _thread_main(self) -> None:
        # Bump the pump thread above normal so WM_INPUT dispatch stays snappy
        # even when the rest of the process is busy. Best-effort: a failure
        # here just leaves it at the default priority.
        try:
            self._kernel32.SetThreadPriority(
                self._kernel32.GetCurrentThread(), _THREAD_PRIORITY_ABOVE_NORMAL
            )
        except Exception:
            pass

        try:
            self._setup_window()
            self._register_raw_input()
            self._apply_clip(*self._clip_center)
        except BaseException as e:
            self._startup_error = e
            self._ready_evt.set()
            self._cleanup_window()
            return

        self._ready_evt.set()
        try:
            self._pump_messages()
        except BaseException as e:
            self._logger.error("Raw input pump crashed", error=str(e))
        finally:
            try:
                self._user32.ClipCursor(None)
            except Exception:
                pass
            self._cleanup_window()

    def _setup_window(self) -> None:
        self._hinstance = self._kernel32.GetModuleHandleW(None)
        wc = _WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wndproc_cb
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = self._hinstance
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = self._class_name
        atom = self._user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            err = self._kernel32.GetLastError()
            raise OSError(f"RegisterClassW failed (err={err})")
        self._wndclass = wc

        hwnd = self._user32.CreateWindowExW(
            0,
            self._class_name,
            "PerpetuaRawInputSink",
            0,
            0,
            0,
            0,
            0,
            _HWND_MESSAGE,
            None,
            self._hinstance,
            None,
        )
        if not hwnd:
            err = self._kernel32.GetLastError()
            raise OSError(f"CreateWindowExW failed (err={err})")
        self._hwnd = hwnd

    def _register_raw_input(self) -> None:
        rid = _RAWINPUTDEVICE()
        rid.usUsagePage = _HID_USAGE_PAGE_GENERIC
        rid.usUsage = _HID_USAGE_GENERIC_MOUSE
        # RIDEV_INPUTSINK: deliver WM_INPUT even when the target HWND is not
        # in the foreground - required since we're a background listener.
        rid.dwFlags = _RIDEV_INPUTSINK
        rid.hwndTarget = self._hwnd
        if not self._user32.RegisterRawInputDevices(
            ctypes.byref(rid), 1, ctypes.sizeof(_RAWINPUTDEVICE)
        ):
            err = self._kernel32.GetLastError()
            raise OSError(f"RegisterRawInputDevices failed (err={err})")

    def _apply_clip(self, cx: int, cy: int) -> None:
        rect = wintypes.RECT(cx, cy, cx + 1, cy + 1)
        if not self._user32.ClipCursor(ctypes.byref(rect)):
            err = self._kernel32.GetLastError()
            self._logger.warning("ClipCursor failed", err=err)

    def _pump_messages(self) -> None:
        msg = wintypes.MSG()
        get_msg = self._user32.GetMessageW
        translate = self._user32.TranslateMessage
        dispatch = self._user32.DispatchMessageW
        p_msg = ctypes.byref(msg)
        while True:
            ret = get_msg(p_msg, None, 0, 0)
            if ret == 0:
                break
            if ret == -1:
                err = self._kernel32.GetLastError()
                self._logger.error("GetMessageW error", err=err)
                break
            translate(p_msg)
            dispatch(p_msg)

    def _cleanup_window(self) -> None:
        if self._hwnd:
            try:
                self._user32.DestroyWindow(self._hwnd)
            except Exception:
                pass
            self._hwnd = 0
        try:
            self._user32.UnregisterClassW(self._class_name, self._hinstance)
        except Exception:
            pass
        self._wndclass = None

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == _WM_INPUT:
                self._handle_wm_input(lparam)
                return self._DefWindowProcW(hwnd, msg, wparam, lparam)
            if msg == _WM_CLOSE:
                self._user32.DestroyWindow(hwnd)
                return 0
            if msg == _WM_DESTROY:
                self._user32.PostQuitMessage(0)
                return 0
        except BaseException as e:
            # Never propagate Python exceptions through a WindowProc: the C
            # caller can't recover and will crash the process.
            try:
                self._logger.error("WindowProc error", error=str(e))
            except Exception:
                pass
        return self._DefWindowProcW(hwnd, msg, wparam, lparam)

    def _handle_wm_input(self, lparam) -> None:
        # Mouse RAWINPUT has a fixed layout, so skip the GetRawInputData size
        # probe and read straight into a pre-allocated buffer. Saves one
        # syscall per WM_INPUT (~1000/s on a 1 kHz gaming mouse).
        self._raw_size.value = _RAWINPUT_SIZE
        rc = self._GetRawInputData(
            lparam,
            _RID_INPUT,
            self._raw_buf_ref,
            self._raw_size_ref,
            _RAWINPUTHEADER_SIZE,
        )
        if rc == 0xFFFFFFFF or rc == 0:
            return
        raw = self._raw_buf
        if raw.header.dwType != _RIM_TYPEMOUSE:
            return
        mouse = raw.mouse
        if mouse.usFlags & _MOUSE_MOVE_ABSOLUTE:
            # Tablets / RDP / some VMs send absolute coordinates here; a
            # regular mouse is always MOUSE_MOVE_RELATIVE.
            return
        dx = mouse.lLastX
        dy = mouse.lLastY
        if not (dx or dy):
            return

        with self._pending_lock:
            self._pending_dx += dx
            self._pending_dy += dy
            already_scheduled = self._pending_scheduled
            self._pending_scheduled = True
        if already_scheduled:
            return
        try:
            self._call_soon(self._drain_cb)
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
        if dx or dy:
            try:
                self._on_delta(dx, dy)
            except Exception as e:
                self._logger.error("on_delta failed", error=str(e))


class ServerMouseListener(_base.ServerMouseListener):
    """
    It listens for mouse events on Windows systems.
    Captures HID-mickey deltas via Raw Input, pins the (blanked) cursor at
    the primary-monitor centre with ClipCursor, and suppresses clicks /
    scroll through pynput's win32 filter while listening.
    """

    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5

    def __init__(self, *args, **kwargs):
        # Force filtering on: the daemon passes filtering=False by default
        # which would let a hidden cursor click through to the desktop.
        kwargs["filtering"] = True
        super().__init__(*args, **kwargs)

        self._cursor_hidden: bool = False
        self._listening_center: tuple[int, int] = (0, 0)
        self._raw_capture: Optional[_RawMouseCapture] = None
        self._user32 = ctypes.windll.user32

        self.event_bus.subscribe(
            event_type=BusEventType.SCREEN_CHANGE_GUARD,
            callback=self._on_screen_change_guard,
            priority=True,
        )
        # Restore the cursor if the active client drops without a clean
        # return-to-server, otherwise the user is stuck with a blank cursor.
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected_show_cursor,
            priority=True,
        )

    async def _on_screen_change_guard(
        self, data: Optional[ActiveScreenChangedEvent]
    ) -> None:
        # Going to client: dispatch ACTIVE_SCREEN_CHANGED first, then start
        # the capture. Returning: stop capture first so the next
        # position_cursor warp lands on a visible cursor.
        if data is None:
            return

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

    async def _enable_capture(self) -> None:
        if self._cursor_hidden:
            return
        # Hide before the warp so the blank doesn't track the warp animation.
        _hide_system_cursors()
        self._listening_center = self._compute_listening_center()
        cx, cy = self._listening_center
        try:
            self._user32.SetCursorPos(cx, cy)
        except Exception as e:
            self._logger.error("SetCursorPos centre failed", error=str(e))

        loop = self._loop
        if loop is None or loop.is_closed():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._logger.error("No event loop available - capture aborted")
                return

        self._raw_capture = _RawMouseCapture(
            loop=loop,
            on_delta=self._on_raw_delta,
            logger=self._logger,
        )
        if not self._raw_capture.start(center=(cx, cy)):
            self._raw_capture = None
            _restore_system_cursors()
            return
        self._cursor_hidden = True

    async def _disable_capture(self) -> None:
        if not self._cursor_hidden:
            return
        self._cursor_hidden = False
        capture = self._raw_capture
        self._raw_capture = None
        if capture is not None:
            # stop() does a blocking join on the pump thread; run it in the
            # executor so the asyncio loop isn't blocked by it.
            try:
                await asyncio.get_running_loop().run_in_executor(None, capture.stop)
            except Exception as e:
                self._logger.error("Raw input stop failed", error=str(e))
        _restore_system_cursors()

    def _on_raw_delta(self, dx: int, dy: int) -> None:
        if not self._cursor_hidden:
            # A final delta arrived after _disable_capture cleared the flag.
            return
        # send_nowait skips create_task + an event-loop tick vs. awaiting
        # stream.send; if the queue is saturated the operator already lost
        # the race, so dropping is the right behaviour.
        if not self.stream.send_nowait(
            MouseEvent(dx=dx, dy=dy, action=MouseEvent.MOVE_ACTION)
        ):
            self._logger.warning("Mouse stream queue full, dropped raw delta")

    def _compute_listening_center(self) -> tuple[int, int]:
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

    def on_move(self, x, y):
        # Raw Input owns the delta-capture path while listening; ClipCursor
        # makes WH_MOUSE_LL rarely fire but this guards against doubles.
        if self._listening:
            return True
        return super().on_move(x, y)

    def _win32_mouse_suppress_filter(self, msg, data):
        if self._listening:
            # 513/514 = left down/up, 516/517 = right, 519/520 = middle,
            # 522/523 = scroll.
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._user32 = ctypes.windll.user32

    def _inject_relative(self, dx: int, dy: int) -> None:
        """Send a relative mouse motion via ``SendInput``.

        pynput's ``Controller.move`` sets an absolute cursor position, which
        first-person games reading relative movement ignore. A
        ``MOUSEEVENTF_MOVE`` event (without ``MOUSEEVENTF_ABSOLUTE``) delivers
        genuine dx/dy deltas that DirectInput and the standard input pipeline
        consume.
        """
        try:
            inp = _INPUT(type=_INPUT_MOUSE)
            inp.mi = _MOUSEINPUT(
                dx=int(dx),
                dy=int(dy),
                mouseData=0,
                dwFlags=_MOUSEEVENTF_MOVE,
                time=0,
                dwExtraInfo=0,
            )
            self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        except Exception as e:
            self._logger.error("relative SendInput injection failed", error=str(e))
            super()._inject_relative(dx, dy)
