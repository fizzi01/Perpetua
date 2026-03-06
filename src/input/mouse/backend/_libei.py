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

"""
libei mouse controller backend for Wayland compositors (GNOME >= 45, KDE Plasma >= 6.1).

liboeffis opens a RemoteDesktop session on the XDG Desktop Portal D-Bus interface
and returns an EIS file descriptor.  That fd is handed to a libei Sender (snegg)
which negotiates seat capabilities and obtains a Device for input emulation.

snegg does not expose scroll methods on Device, so ei_device_scroll_discrete
is called directly through the C bindings.
"""

import os
import time
import threading
import enum
import select as _select
from evdev import ecodes
from snegg.ei import Sender, EventType, DeviceCapability
from snegg.c.libei import libei
from snegg.oeffis import Oeffis, DeviceType, DisconnectedError, SessionClosedError

Button = enum.Enum(
    "Button",
    module=__name__,
    names=[("unknown", None), ("left", 1), ("middle", 2), ("right", 3)],
)

ButtonToEcodeMap = {
    Button.left.name: ecodes.BTN_LEFT,
    Button.middle.name: ecodes.BTN_MIDDLE,
    Button.right.name: ecodes.BTN_RIGHT,
}

_CAPABILITIES = (
    DeviceCapability.POINTER,
    DeviceCapability.POINTER_ABSOLUTE,
    DeviceCapability.BUTTON,
    DeviceCapability.SCROLL,
)


def _now_us() -> int:
    return int(time.monotonic() * 1_000_000)


class _EiConnection:
    """Singleton that owns the portal session, the libei Sender, and the
    background dispatch thread.  Reconnects transparently when the
    compositor drops the device (e.g. after idle timeout)."""

    def __init__(self):
        self._reset()

    def _reset(self):
        self._device = None
        self._sender: Sender | None = None
        self._oeffis: Oeffis | None = None
        self._eis_file = None
        self._paused = threading.Event()
        self._error: Exception | None = None
        self._dispatch_thread: threading.Thread | None = None

    @property
    def device(self):
        if self._error is not None:
            raise self._error
        return self._device

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def reconnect(self):
        self._reset()
        self.connect()

    def connect(self):
        if self._device is not None:
            return

        poller = _select.poll()

        # Portal session
        self._oeffis = Oeffis.create(devices=DeviceType.POINTER)
        poller.register(self._oeffis.fd, _select.POLLIN)

        # Obtain EIS fd
        eis_fd = None
        for _ in range(50):
            if poller.poll(200):
                try:
                    self._oeffis.dispatch()
                except (DisconnectedError, SessionClosedError) as exc:
                    raise RuntimeError(
                        f"libei: portal rejected connection: {exc}"
                    ) from exc
                try:
                    eis_fd = self._oeffis.eis_fd
                    if eis_fd is not None:
                        break
                except (DisconnectedError, SessionClosedError) as exc:
                    raise RuntimeError(f"libei: portal disconnected: {exc}") from exc
                except AttributeError:
                    continue

        if eis_fd is None:
            raise RuntimeError("libei: failed to obtain EIS fd from portal")

        poller.unregister(self._oeffis.fd)

        # Libei Sender
        self._eis_file = os.fdopen(eis_fd, "rb", closefd=False)
        self._sender = Sender.create_for_fd(
            self._eis_file, name="perpetua-mouse-controller"
        )
        poller.register(self._sender.fd, _select.POLLIN)

        # Seat bind and device acquisition loop
        seat_bound = False
        device = None
        for _ in range(100):
            if poller.poll(100):
                self._sender.dispatch()
            for event in self._sender.events:
                if event.event_type == EventType.SEAT_ADDED and not seat_bound:
                    event.seat.bind(_CAPABILITIES)
                    seat_bound = True
                elif event.event_type == EventType.DEVICE_ADDED and device is None:
                    device = event.device
                    device.start_emulating(0)
                elif (
                    event.event_type == EventType.DEVICE_RESUMED and device is not None
                ):
                    self._device = device
                    self._start_dispatch()
                    return

        raise RuntimeError("libei: no device received after seat bind")

    def _start_dispatch(self):
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, daemon=True
        )
        self._dispatch_thread.start()

    def _dispatch_loop(self):
        poller = _select.poll()
        poller.register(self._sender.fd, _select.POLLIN)

        while self._error is None:
            try:
                ready = poller.poll(500)
            except Exception:
                break

            if not ready:
                continue

            try:
                self._sender.dispatch()
            except Exception as exc:
                self._error = RuntimeError(f"libei: dispatch error: {exc}")
                break

            for event in self._sender.events:
                etype = event.event_type

                if etype == EventType.DEVICE_PAUSED:
                    self._paused.set()

                elif etype == EventType.DEVICE_RESUMED:
                    self._paused.clear()
                    if self._device is not None:
                        self._device.start_emulating(0)

                elif etype == EventType.DEVICE_REMOVED:
                    self._error = RuntimeError("libei: device removed by compositor")
                    self._device = None
                    return

                elif etype == EventType.DISCONNECT:
                    self._error = RuntimeError("libei: disconnected by compositor")
                    self._device = None
                    return


_conn: _EiConnection | None = None
_conn_lock = threading.Lock()


def _get_connection() -> _EiConnection:
    global _conn
    with _conn_lock:
        if _conn is None:
            _conn = _EiConnection()
            _conn.connect()
        return _conn


def _reconnect() -> _EiConnection:
    global _conn
    with _conn_lock:
        _conn = _EiConnection()
        _conn.connect()
        return _conn


def _scroll_delta(device, dx: float, dy: float):
    libei.device_scroll_delta(device._cobject, dx, dy)


def _scroll_discrete(device, dx: int, dy: int):
    libei.device_scroll_discrete(device._cobject, dx, dy)


class MouseController:
    def __init__(self):
        self._x = 0
        self._y = 0
        self._conn = _get_connection()

    def _ensure_device(self):
        if self._conn._error is not None:
            self._conn = _reconnect()
        return self._conn.device

    @property
    def position(self) -> tuple[int, int]:
        return (self._x, self._y)

    @position.setter
    def position(self, value: tuple[int, int]):
        x, y = int(value[0]), int(value[1])
        if not self._conn.paused:
            self._ensure_device().pointer_motion_absolute(float(x), float(y)).frame(
                _now_us()
            )
        self._x = x
        self._y = y

    def move(self, dx: int, dy: int):
        dx, dy = int(dx), int(dy)
        self._x += dx
        self._y += dy
        if not self._conn.paused:
            self._ensure_device().pointer_motion_absolute(
                float(self._x), float(self._y)
            ).frame(_now_us())

    def press(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        if code is not None and not self._conn.paused:
            self._ensure_device().button_button(code, True).frame(_now_us())

    def release(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        if code is not None and not self._conn.paused:
            self._ensure_device().button_button(code, False).frame(_now_us())

    def click(self, button: Button, count: int = 1):
        for _ in range(count):
            self.press(button)
            self.release(button)

    def scroll(self, dx: int, dy: int):
        if not self._conn.paused and (dx or dy):
            # 1 wheel click = 120 hi-res units
            device = self._ensure_device()
            _scroll_discrete(device, int(dx) * 120, int(dy) * 120)
            device.frame(_now_us())


__all__ = ["MouseController", "Button"]
