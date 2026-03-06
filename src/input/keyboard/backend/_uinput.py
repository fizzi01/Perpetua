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

from typing import Optional
import threading
import asyncio
import uvloop
import evdev
from evdev import UInput, ecodes, KeyEvent
from pynput.keyboard._uinput import LAYOUT, KeyCode, Key

from input.utils import _wrap

KEY_MAX = 767  # kernel KEY_MAX; codes above this are rejected by uinput


class KeyboardController:
    """
    UInput keyboard controller for Linux.

    Injects keyboard events via a virtual uinput device
    """

    _MAP_MODIFIERS: dict[str, int] = {
        k.name: k.value.vk for k in Key if k.value.vk is not None
    }

    class InvalidKeyException(Exception):
        pass

    def __init__(self):
        self._dev = evdev.UInput(name="perpetua-keyboard")
        self._layout = LAYOUT

    def __del__(self):
        if hasattr(self, "_dev"):
            self._dev.close()

    def press(self, key):
        """Press a key (Key enum or KeyCode)."""
        self._handle(self._resolve(key), True)

    def release(self, key):
        """Release a key (Key enum or KeyCode)."""
        self._handle(self._resolve(key), False)

    def _resolve(self, key) -> KeyCode | Key:
        """Normalize a Key enum to its underlying KeyCode."""
        if isinstance(key, Key):
            return key.value
        return key

    def _handle(self, key: KeyCode | Key, is_press: bool):
        try:
            vk = self._to_vk(key)
        except ValueError:
            raise self.InvalidKeyException(key)

        try:
            self._dev.write(ecodes.EV_KEY, vk, int(is_press))
        finally:
            self._dev.syn()

    def _to_vk(self, key: KeyCode | Key) -> int:
        """Resolve a key to its virtual key code.

        Character keys are mapped via the layout to their base vk code.
        Modifier keys are mapped to their canonical vk code.
        """
        if hasattr(key, "vk") and key.vk is not None:
            return key.vk
        elif hasattr(key, "char") and key.char is not None:
            vk, _ = self._layout.for_char(key.char)
            return vk
        elif hasattr(key, "name") and key.name in self._MAP_MODIFIERS:
            return self._MAP_MODIFIERS[key.name]
        raise ValueError(key)


def find_keyboards(devices: Optional[list[str]] = None) -> list[evdev.InputDevice]:
    """Return all /dev/input/eventX devices that look like keyboards."""
    result = []
    for path in evdev.list_devices():
        if devices and path not in devices:
            continue
        try:
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            keys = caps.get(ecodes.EV_KEY, [])
            if ecodes.KEY_A in keys and ecodes.KEY_SPACE in keys:
                result.append(dev)
        except (PermissionError, OSError):
            pass
    return result


def make_uinput(keyboards: list[evdev.InputDevice]) -> UInput:
    """Create a UInput device that mirrors the union of all keyboard keys."""
    all_keys: set[int] = set()
    for dev in keyboards:
        caps = dev.capabilities()
        for code in caps.get(ecodes.EV_KEY, []):
            if isinstance(code, int) and code <= KEY_MAX:
                all_keys.add(code)
    return UInput(
        name="perpetua-keyboard",
        events={ecodes.EV_KEY: sorted(all_keys)},
    )


class KeyboardListener(threading.Thread):
    """
    UInput keyboard listener backend.
    Forwards EV_KEY events from grabbed physical keyboards to a virtual UInput device.
    """

    _MODIFIERS = {
        Key.shift.value.vk: Key.shift,
        Key.shift_l.value.vk: Key.shift,
        Key.shift_r.value.vk: Key.shift,
        Key.alt.value.vk: Key.alt,
        Key.alt_l.value.vk: Key.alt,
        Key.alt_r.value.vk: Key.alt,
        Key.alt_gr.value.vk: Key.alt_gr,
    }

    def __init__(
        self,
        on_press=None,
        on_release=None,
        xorg_filter=None,
        suppress=False,
        devices: Optional[list[str]] = None,
    ):
        from utils.logging import get_logger

        super().__init__(daemon=True)
        self.on_press = _wrap(on_press, 2)
        self.on_release = _wrap(on_release, 2)
        self._xorg_filter = _wrap(xorg_filter, 1)
        self._suppress = suppress
        self._running = threading.Event()
        self._running.clear()
        self._devices = find_keyboards(devices) if devices else find_keyboards()
        self._ui = None
        self._layout = LAYOUT
        self._modifiers = set()
        try:
            self._ui = make_uinput(self._devices) if not suppress else None
        except Exception as e:
            self._logger.error(f"Failed to create UInput device ({e})")
            raise
        self._loop = None
        self._cleanup_done = threading.Event()

        self._logger = get_logger(self.__class__.__name__)

    @property
    def suppress(self):
        return self._suppress

    @suppress.setter
    def suppress(self, value):
        self._suppress = value

    def run(self):
        self._running.set()
        try:
            for dev in self._devices:
                dev.grab()
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
            asyncio.set_event_loop(self._loop)
            tasks = [self._forward(dev) for dev in self._devices]
            self._loop.run_until_complete(asyncio.gather(*tasks))
        except asyncio.CancelledError:
            pass
        except RuntimeError as e:
            if "Event loop is closed" in str(e):
                pass  # Expected on shutdown
        except Exception as e:
            self._logger.error(f"Error in thread ({e})")
        finally:
            for dev in self._devices:
                self._logger.debug(f"Ungrab: {dev.path}  ({dev.name})")
                try:
                    dev.ungrab()
                except Exception:
                    pass
            if self._ui:
                self._logger.debug("Closing UInput device")
                self._ui.close()
            self._cleanup_done.set()

    def map_key(self, vk):
        try:
            key = self._layout.for_vk(vk, self._modifiers)
            # Backspace can be sent as KEY_BACKSPACE or KEY_DELETE, so we normalize it to the former
            if key == Key.delete and vk == ecodes.KEY_BACKSPACE:
                key = Key.backspace
        except KeyError:
            try:
                key = next(key for key in Key if key.value.vk == vk)
            except StopIteration:
                key = KeyCode.from_vk(vk)

        return key

    async def _forward(self, dev):
        async for event in dev.async_read_loop():
            # Print event class name
            if event.type == ecodes.EV_KEY:
                pressed = event.value in (KeyEvent.key_down, KeyEvent.key_hold)
                vk = event.code

                if vk in self._MODIFIERS:
                    modifier = self._MODIFIERS[vk]
                    if pressed:
                        self._modifiers.add(modifier)
                    elif modifier in self._modifiers:
                        self._modifiers.remove(modifier)

                key = self.map_key(vk)

                try:
                    if pressed:
                        if self.on_press(key, False) is False:
                            self.stop()
                            break
                    else:
                        if self.on_release(key, False) is False:
                            self.stop()
                            break
                except Exception as e:
                    self._logger.error(f"Error in event handler ({e})")
                if self._xorg_filter:
                    res = self._xorg_filter(event)
                    if res is False or res is None:
                        continue
                    elif isinstance(res, evdev.events.InputEvent):
                        # Let the filter modify the event if it returns a new one
                        event = res
                if not self._suppress and self._ui:
                    self._ui.write_event(event)
                    self._ui.syn()
            if not self._running.is_set():
                break

    def is_alive(self):
        return self._running.is_set() and super().is_alive()

    def start(self):
        super().start()

    def stop(self):
        self._running.clear()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def join(self, timeout=None):
        # self.stop()
        self._cleanup_done.wait(timeout)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, value, traceback):
        self.stop()
        self.join()


async def test():
    import signal

    @staticmethod
    def _get_key(key: Key | KeyCode) -> str:
        """
        Helper to convert pynput Key or KeyCode to string representation.
        """
        if isinstance(key, KeyCode):
            return key.char if key.char is not None else f"vk_{key.vk}"
        elif isinstance(key, Key):
            return key.name if key.name is not None else f"vk_{key.value.vk}"

        raise AttributeError(f"Key {key} is not a valid key.")

    # Example usage
    def on_press(key, injected):
        parsed = _get_key(key)
        print(f"Pressed: {parsed} injected={injected}")

    def on_release(key, injected):
        parsed = _get_key(key)
        print(f"Released: {parsed} injected={injected}")

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def signal_handler():
        stop_event.set()

    loop.add_signal_handler(signal.SIGINT, signal_handler)

    def signal_handler():
        stop_event.set()

    with KeyboardListener(on_press=on_press, on_release=on_release) as lst:
        lst.join()
        print("Stopping listener...")
