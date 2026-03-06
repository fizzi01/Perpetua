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
Pure uinput mouse backend for Linux.

Provides:
- MouseListener  – grabs physical mice, forwards events through a virtual
                   uinput device, and fires callbacks.
- MouseController – injects relative mouse events via uinput (EV_REL/EV_KEY).
                    No absolute positioning (use the libei backend for that).
- Button / mapping constants shared across backends.
"""

import threading
import enum

import evdev
from evdev import UInput, ecodes

from input.utils import _wrap

# ---------------------------------------------------------------------------
# Button enum & mapping tables (shared with other backends)
# ---------------------------------------------------------------------------

Button = enum.Enum(
    "Button",
    module=__name__,
    names=[("unknown", None), ("left", 1), ("middle", 2), ("right", 3)],
)

RawButtonMap = {
    ecodes.BTN_LEFT: Button.left,
    ecodes.BTN_MIDDLE: Button.middle,
    ecodes.BTN_RIGHT: Button.right,
}

ButtonToEcodeMap = {
    Button.left.name: ecodes.BTN_LEFT,
    Button.middle.name: ecodes.BTN_MIDDLE,
    Button.right.name: ecodes.BTN_RIGHT,
}

# ---------------------------------------------------------------------------
# Device discovery helpers
# ---------------------------------------------------------------------------


def find_mice(devices: list[str] = None) -> list[evdev.InputDevice]:
    """Return all /dev/input/eventX devices that look like mice."""
    result = []
    for path in evdev.list_devices():
        if devices and path not in devices:
            continue
        try:
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            has_rel = ecodes.EV_REL in caps
            has_btn = ecodes.EV_KEY in caps and any(
                btn in caps[ecodes.EV_KEY]
                for btn in (ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE)
            )
            if has_rel and has_btn:
                result.append(dev)
        except (PermissionError, OSError):
            pass
    return result


def make_uinput(mice: list[evdev.InputDevice]) -> UInput:
    """Create a UInput device that mirrors the union of all mouse caps."""
    all_events: dict[int, set[int]] = {}
    for dev in mice:
        caps = dev.capabilities()
        for etype, codes in caps.items():
            if etype not in all_events:
                all_events[etype] = set()
            all_events[etype].update(codes)
    filtered = {
        etype: sorted(codes)
        for etype, codes in all_events.items()
        if etype in (ecodes.EV_KEY, ecodes.EV_REL, ecodes.EV_MSC)
    }
    return UInput(name="perpetua-mouse", events=filtered)


# ---------------------------------------------------------------------------
# Controller (uinput-only, relative movement)
# ---------------------------------------------------------------------------

_CONTROLLER_EVENTS = {
    ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_MIDDLE, ecodes.BTN_RIGHT],
    ecodes.EV_REL: [
        ecodes.REL_X,
        ecodes.REL_Y,
        ecodes.REL_WHEEL,
        ecodes.REL_HWHEEL,
        ecodes.REL_WHEEL_HI_RES,
        ecodes.REL_HWHEEL_HI_RES,
    ],
}


class MouseController:
    """Uinput-only mouse controller (relative movement, no absolute positioning)."""

    def __init__(self):
        self._x = 0
        self._y = 0
        self._ui = UInput(name="perpetua-mouse-controller", events=_CONTROLLER_EVENTS)

    @property
    def position(self) -> tuple[int, int]:
        return (self._x, self._y)

    @position.setter
    def position(self, value: tuple[int, int]):
        x, y = int(value[0]), int(value[1])
        dx, dy = x - self._x, y - self._y
        if dx or dy:
            self._ui.write(ecodes.EV_REL, ecodes.REL_X, dx)
            self._ui.write(ecodes.EV_REL, ecodes.REL_Y, dy)
            self._ui.syn()
        self._x = x
        self._y = y

    def move(self, dx: int, dy: int):
        dx, dy = int(dx), int(dy)
        self._ui.write(ecodes.EV_REL, ecodes.REL_X, dx)
        self._ui.write(ecodes.EV_REL, ecodes.REL_Y, dy)
        self._ui.syn()
        self._x += dx
        self._y += dy

    def press(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        if code is not None:
            self._ui.write(ecodes.EV_KEY, code, 1)
            self._ui.syn()

    def release(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        if code is not None:
            self._ui.write(ecodes.EV_KEY, code, 0)
            self._ui.syn()

    def click(self, button: Button, count: int = 1):
        for _ in range(count):
            self.press(button)
            self.release(button)

    def scroll(self, dx: int, dy: int):
        if dx:
            self._ui.write(ecodes.EV_REL, ecodes.REL_HWHEEL, int(dx))
            self._ui.write(ecodes.EV_REL, ecodes.REL_HWHEEL_HI_RES, int(dx) * 120)
        if dy:
            self._ui.write(ecodes.EV_REL, ecodes.REL_WHEEL, int(dy))
            self._ui.write(ecodes.EV_REL, ecodes.REL_WHEEL_HI_RES, int(dy) * 120)
        self._ui.syn()


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------


class MouseListener(threading.Thread):
    """Uinput mouse listener backend.

    Grabs physical mouse devices, fires callbacks, and optionally forwards
    events through a virtual uinput device.

    Args:
        on_move:   callable(x, y, injected)
        on_click:  callable(x, y, button, pressed, injected)
        on_scroll: callable(x, y, dx, dy, injected)
        suppress:  if True, do not forward events to UInput
    """

    def __init__(
        self,
        on_move=None,
        on_click=None,
        on_scroll=None,
        suppress=False,
        devices: list[str] = None,
    ):
        super().__init__(daemon=True)
        self.on_move = _wrap(on_move, 3)
        self.on_click = _wrap(on_click, 5)
        self.on_scroll = _wrap(on_scroll, 5)
        self._suppress = suppress
        self._running = threading.Event()
        self._running.clear()
        self._devices = find_mice(devices) if devices else find_mice()
        self._ui = make_uinput(self._devices) if not suppress else None
        self._display = None
        self._injected_flag = False
        self._injected_rel_count = 0
        self._injected_key_count = 0
        self._cleanup_done = threading.Event()

    @property
    def suppress(self):
        return self._suppress

    @suppress.setter
    def suppress(self, value):
        self._suppress = value

    def _get_position(self):
        if not self._display:
            return (0, 0)
        try:
            with display_manager(self._display) as d:
                root = d.screen().root
                pointer = root.query_pointer()
                return (pointer.root_x, pointer.root_y)
        except Exception:
            return X11Error("Failed to get pointer position from X11")

    def run(self):
        import select

        self._running.set()
        self._display = display.Display()
        try:
            for dev in self._devices:
                dev.grab()
            poller = select.poll()
            fd_to_dev = {}
            for dev in self._devices:
                poller.register(dev.fd, select.POLLIN)
                fd_to_dev[dev.fd] = dev
            while self._running.is_set():
                events = poller.poll(1)
                for fd, flag in events:
                    if flag & select.POLLIN:
                        dev = fd_to_dev[fd]
                        for event in dev.read():
                            self._handle_event(dev, event)
        except Exception as e:
            print(f"MouseListener error: {e}")
        finally:
            for dev in self._devices:
                print(f"Ungrab: {dev.path}  ({dev.name})")
                try:
                    dev.ungrab()
                except Exception:
                    pass
            if self._ui:
                print("Closing UInput device")
                self._ui.close()
            self._cleanup_done.set()

    def _handle_event(self, dev, event):
        if (
            event.type == ecodes.EV_MSC
            and event.code == ecodes.MSC_SCAN
            and event.value == 0x1337
        ):
            self._injected_flag = True
            self._injected_rel_count = 2
            self._injected_key_count = 1
            return

        injected = self._injected_flag

        if self._injected_flag:
            if event.type == ecodes.EV_REL:
                self._injected_rel_count -= 1
                if self._injected_rel_count <= 0:
                    self._injected_flag = False
            elif event.type == ecodes.EV_KEY:
                self._injected_key_count -= 1
                if self._injected_key_count <= 0:
                    self._injected_flag = False

        if event.type == ecodes.EV_REL and event.code in (ecodes.REL_X, ecodes.REL_Y):
            if self.on_move:
                pos = self._get_position()
                if self.on_move(pos[0], pos[1], injected) is False:
                    self.stop()
                    return
        elif event.type == ecodes.EV_KEY:
            if event.code in RawButtonMap:
                pressed = event.value == 1
                if self.on_click:
                    pos = self._get_position()
                    if (
                        self.on_click(
                            pos[0], pos[1], RawButtonMap[event.code], pressed, injected
                        )
                        is False
                    ):
                        self.stop()
                        return
        elif event.type == ecodes.EV_REL and event.code in (
            ecodes.REL_WHEEL,
            ecodes.REL_HWHEEL,
            ecodes.REL_WHEEL_HI_RES,
        ):
            dx = event.value if event.code == ecodes.REL_HWHEEL else 0
            dy = event.value if event.code == ecodes.REL_WHEEL else 0
            if self.on_scroll:
                pos = self._get_position()
                if self.on_scroll(pos[0], pos[1], dx, dy, injected) is False:
                    self.stop()
                    return

        if not self._suppress and self._ui:
            self._ui.write_event(event)
            self._ui.syn()

    def start(self):
        super().start()

    def stop(self):
        self._running.clear()

    def join(self, timeout=5):
        self._cleanup_done.wait(timeout)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, value, traceback):
        self.stop()
        self.join()


__all__ = [
    "MouseListener",
    "MouseController",
    "Button",
    "ButtonToEcodeMap",
    "RawButtonMap",
]
