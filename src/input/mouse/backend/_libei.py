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

Provides absolute pointer positioning, button events, and relative movement
through the portal's POINTER_ABSOLUTE capability.  Scroll events are delegated
to a uinput fallback device since libei's Device API does not expose scroll
methods.
"""

import os
import select as _select

from snegg.ei import Sender, EventType, DeviceCapability
from snegg.oeffis import Oeffis, DeviceType, DisconnectedError, SessionClosedError
from evdev import UInput, ecodes

from ._uinput import Button, ButtonToEcodeMap

# ---------------------------------------------------------------------------
# libei connection singleton
# ---------------------------------------------------------------------------

_ei_device = None
_ei_sender = None  # Must stay alive to keep the libei connection open
_oeffis_ctx = None


def _connect():
    """Connect to the compositor via XDG Desktop Portal and return a libei Device.

    The connection objects are cached as module-level singletons so that a
    single portal session is reused for the lifetime of the process.
    """
    global _ei_device, _ei_sender, _oeffis_ctx

    if _ei_device is not None:
        return _ei_device

    poller = _select.poll()

    # 1. Open a portal session for pointer emulation
    _oeffis_ctx = Oeffis.create(devices=DeviceType.POINTER)
    poller.register(_oeffis_ctx.fd, _select.POLLIN)

    # 2. Wait until the portal hands us an EIS file descriptor
    eis_fd = None
    for _ in range(50):
        if poller.poll(200):
            try:
                _oeffis_ctx.dispatch()
            except (DisconnectedError, SessionClosedError) as e:
                raise RuntimeError(f"libei: portal rejected connection: {e}") from e
            try:
                eis_fd = _oeffis_ctx.eis_fd
                if eis_fd is not None:
                    break
            except (DisconnectedError, SessionClosedError) as e:
                raise RuntimeError(f"libei: portal disconnected: {e}") from e
            except AttributeError:
                continue

    if eis_fd is None:
        raise RuntimeError("libei: failed to obtain EIS fd from portal")

    poller.unregister(_oeffis_ctx.fd)

    # 3. Create a Sender from the portal fd
    eis_file = os.fdopen(eis_fd, "rb", closefd=False)
    _ei_sender = Sender.create_for_fd(eis_file, name="perpetua-mouse-controller")
    poller.register(_ei_sender.fd, _select.POLLIN)

    # 4. Bind capabilities and wait for device
    caps = (
        DeviceCapability.POINTER,
        DeviceCapability.POINTER_ABSOLUTE,
        DeviceCapability.BUTTON,
        DeviceCapability.SCROLL,
    )

    seat_bound = False
    device = None
    for _ in range(100):
        if poller.poll(100):
            _ei_sender.dispatch()
        for event in _ei_sender.events:
            if event.event_type == EventType.SEAT_ADDED and not seat_bound:
                event.seat.bind(caps)
                seat_bound = True
            elif event.event_type == EventType.DEVICE_ADDED and device is None:
                device = event.device
                device.start_emulating()
            elif event.event_type == EventType.DEVICE_RESUMED and device is not None:
                _ei_device = device
                return device

    raise RuntimeError("libei: no device received after seat bind")


# ---------------------------------------------------------------------------
# uinput fallback events (scroll only)
# ---------------------------------------------------------------------------

_SCROLL_EVENTS = {
    ecodes.EV_REL: [
        ecodes.REL_WHEEL,
        ecodes.REL_HWHEEL,
        ecodes.REL_WHEEL_HI_RES,
        ecodes.REL_HWHEEL_HI_RES,
    ],
}


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class MouseController:
    """Mouse controller for compositors with libei/XDG Portal support (GNOME).

    Uses the portal's POINTER_ABSOLUTE capability for cursor positioning and
    button events.  Scroll is handled via a uinput EV_REL device since the
    libei Device API has no scroll methods.
    """

    def __init__(self):
        self._x = 0
        self._y = 0
        self._device = _connect()
        self._scroll_ui = UInput(name="perpetua-mouse-scroll", events=_SCROLL_EVENTS)

    # -- position ----------------------------------------------------------

    @property
    def position(self) -> tuple[int, int]:
        return (self._x, self._y)

    @position.setter
    def position(self, value: tuple[int, int]):
        x, y = int(value[0]), int(value[1])
        self._device.pointer_motion_absolute(float(x), float(y)).frame()
        self._x = x
        self._y = y

    def move(self, dx: int, dy: int):
        dx, dy = int(dx), int(dy)
        self._x += dx
        self._y += dy
        self._device.pointer_motion_absolute(float(self._x), float(self._y)).frame()

    # -- buttons -----------------------------------------------------------

    def press(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        if code is not None:
            self._device.button_button(code, True).frame()

    def release(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        if code is not None:
            self._device.button_button(code, False).frame()

    def click(self, button: Button, count: int = 1):
        for _ in range(count):
            self.press(button)
            self.release(button)

    # -- scroll ------------------------------------------------------------

    def scroll(self, dx: int, dy: int):
        if dx:
            self._scroll_ui.write(ecodes.EV_REL, ecodes.REL_HWHEEL, int(dx))
            self._scroll_ui.write(ecodes.EV_REL, ecodes.REL_HWHEEL_HI_RES, int(dx) * 120)
        if dy:
            self._scroll_ui.write(ecodes.EV_REL, ecodes.REL_WHEEL, int(dy))
            self._scroll_ui.write(ecodes.EV_REL, ecodes.REL_WHEEL_HI_RES, int(dy) * 120)
        self._scroll_ui.syn()


__all__ = ["MouseController", "Button"]
