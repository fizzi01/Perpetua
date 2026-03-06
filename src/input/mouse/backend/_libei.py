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
Mouse controller backend using libei (via snegg) for compositors that support
the XDG Desktop Portal input-emulation interface (GNOME, etc.).
"""

import os
import time
import threading
import select as _select

from snegg.ei import Sender, EventType, DeviceCapability
from snegg.oeffis import Oeffis, DeviceType, DisconnectedError, SessionClosedError
from evdev import UInput, ecodes

from ._uinput import Button, ButtonToEcodeMap

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CAPABILITIES = (
    DeviceCapability.POINTER,
    DeviceCapability.POINTER_ABSOLUTE,
    DeviceCapability.BUTTON,
    DeviceCapability.SCROLL,
)


def _now_us() -> int:
    """Current wall-clock time in microseconds (for libei frame timestamps)."""
    return int(time.monotonic() * 1_000_000)


# ---------------------------------------------------------------------------
# Connection manager (singleton)
# ---------------------------------------------------------------------------


class _EiConnection:
    """Manages the libei connection lifecycle.

    Handles the XDG Desktop Portal handshake, keeps the Sender alive, and
    runs a background thread that dispatches compositor events so that
    DEVICE_PAUSED / DEVICE_RESUMED / DISCONNECTED are processed promptly.
    """

    def __init__(self):
        self._device = None
        self._sender: Sender | None = None
        self._oeffis: Oeffis | None = None
        self._eis_file = None

        self._paused = threading.Event()  # set = paused
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

    def connect(self):
        """Run the full portal >EIS >seat >device handshake."""
        if self._device is not None:
            return

        poller = _select.poll()

        # 1. Portal session
        self._oeffis = Oeffis.create(devices=DeviceType.POINTER)
        poller.register(self._oeffis.fd, _select.POLLIN)

        # 2. Obtain EIS fd
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

        # 3. Sender
        self._eis_file = os.fdopen(eis_fd, "rb", closefd=False)
        self._sender = Sender.create_for_fd(
            self._eis_file, name="perpetua-mouse-controller"
        )
        poller.register(self._sender.fd, _select.POLLIN)

        # 4. Seat bind >device
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

    # background event dispatch

    def _start_dispatch(self):
        """Spawn a daemon thread that processes compositor lifecycle events."""
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

                elif etype == EventType.DISCONNECTED:
                    self._error = RuntimeError("libei: disconnected by compositor")
                    self._device = None
                    return


# Module-level singleton
_conn: _EiConnection | None = None


def _get_connection() -> _EiConnection:
    global _conn
    if _conn is None:
        _conn = _EiConnection()
        _conn.connect()
    return _conn


_SCROLL_EVENTS = {
    ecodes.EV_REL: [
        ecodes.REL_WHEEL,
        ecodes.REL_HWHEEL,
        ecodes.REL_WHEEL_HI_RES,
        ecodes.REL_HWHEEL_HI_RES,
    ],
}


class MouseController:
    """Mouse controller for compositors with libei/XDG Portal support (GNOME).

    Uses the portal's POINTER_ABSOLUTE capability for cursor positioning and
    button events.  Scroll is handled via a uinput EV_REL device since the
    snegg Device API has no scroll methods.
    """

    def __init__(self):
        self._x = 0
        self._y = 0
        self._conn = _get_connection()
        self._scroll_ui = UInput(name="perpetua-mouse", events=_SCROLL_EVENTS)

    @property
    def _device(self):
        return self._conn.device

    @property
    def position(self) -> tuple[int, int]:
        return (self._x, self._y)

    @position.setter
    def position(self, value: tuple[int, int]):
        x, y = int(value[0]), int(value[1])
        if not self._conn.paused:
            self._device.pointer_motion_absolute(float(x), float(y)).frame(_now_us())
        self._x = x
        self._y = y

    def move(self, dx: int, dy: int):
        dx, dy = int(dx), int(dy)
        self._x += dx
        self._y += dy
        if not self._conn.paused:
            self._device.pointer_motion_absolute(float(self._x), float(self._y)).frame(
                _now_us()
            )

    def press(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        if code is not None and not self._conn.paused:
            self._device.button_button(code, True).frame(_now_us())

    def release(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        if code is not None and not self._conn.paused:
            self._device.button_button(code, False).frame(_now_us())

    def click(self, button: Button, count: int = 1):
        for _ in range(count):
            self.press(button)
            self.release(button)

    def scroll(self, dx: int, dy: int):
        if dx:
            self._scroll_ui.write(ecodes.EV_REL, ecodes.REL_HWHEEL, int(dx))
            self._scroll_ui.write(
                ecodes.EV_REL, ecodes.REL_HWHEEL_HI_RES, int(dx) * 120
            )
        if dy:
            self._scroll_ui.write(ecodes.EV_REL, ecodes.REL_WHEEL, int(dy))
            self._scroll_ui.write(ecodes.EV_REL, ecodes.REL_WHEEL_HI_RES, int(dy) * 120)
        self._scroll_ui.syn()


__all__ = ["MouseController", "Button"]
