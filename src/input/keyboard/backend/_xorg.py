import unicodedata
import enum
from typing import Optional
import threading
import asyncio
import uvloop
import evdev
from evdev import UInput, ecodes

from input.utils import _wrap

KEY_MAX = 767  # kernel KEY_MAX; codes above this are rejected by uinput


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
        name="perpetua-test-keyboard",
        events={ecodes.EV_KEY: sorted(all_keys)},
    )


class KeyCode(object):
    def __init__(self, code: int, char: Optional[str] = None, is_dead: bool = False):
        self.vk = code
        self.char = char
        if self.char:
            self.char = self.char.replace(
                "KEY_", ""
            ).lower()  # Clean up char if it has "KEY_" prefix
        self.is_dead = is_dead

        if self.is_dead:
            try:
                self.combining = unicodedata.lookup(
                    "COMBINING " + unicodedata.name(self.char)  # ty:ignore[invalid-argument-type]
                )
            except KeyError:
                self.is_dead = False
                self.combining = None
            if self.is_dead and not self.combining:
                raise KeyError(char)
        else:
            self.combining = None

    def __repr__(self):
        if self.is_dead:
            return "[%s]" % repr(self.char)
        if self.char is not None:
            return repr(self.char)
        else:
            return "<%d>" % self.vk

    def __str__(self):
        return repr(self)

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        if self.char is not None and other.char is not None:
            return self.char == other.char and self.is_dead == other.is_dead
        return self.vk == other.vk

    def __hash__(self):
        return hash(repr(self))

    @classmethod
    def from_vk(cls, vk, **kwargs):
        """Creates a key from a virtual key code.

        :param vk: The virtual key code.

        :param kwargs: Any other parameters to pass.

        :return: a key code
        """
        return cls(code=vk, **kwargs)

    @classmethod
    def from_char(cls, char, **kwargs):
        """Creates a key from a character.

        :param str char: The character.

        :return: a key code
        """
        return cls(char=char, **kwargs)

    @classmethod
    def from_dead(cls, char, **kwargs):
        """Creates a dead key.

        :param char: The dead key. This should be the unicode character
            representing the stand alone character, such as ``'~'`` for
            *COMBINING TILDE*.

        :return: a key code
        """
        return cls(char=char, is_dead=True, **kwargs)


class Key(enum.Enum):
    alt = evdev.ecodes.KEY_LEFTALT
    alt_l = evdev.ecodes.KEY_LEFTALT
    alt_r = evdev.ecodes.KEY_RIGHTALT
    alt_gr = evdev.ecodes.KEY_RIGHTALT
    backspace = evdev.ecodes.KEY_BACKSPACE
    caps_lock = evdev.ecodes.KEY_CAPSLOCK
    cmd = evdev.ecodes.KEY_LEFTMETA
    cmd_l = evdev.ecodes.KEY_LEFTMETA
    cmd_r = evdev.ecodes.KEY_RIGHTMETA
    ctrl = evdev.ecodes.KEY_LEFTCTRL
    ctrl_l = evdev.ecodes.KEY_LEFTCTRL
    ctrl_r = evdev.ecodes.KEY_RIGHTCTRL
    delete = evdev.ecodes.KEY_DELETE
    down = evdev.ecodes.KEY_DOWN
    end = evdev.ecodes.KEY_END
    enter = evdev.ecodes.KEY_ENTER
    esc = evdev.ecodes.KEY_ESC
    f1 = evdev.ecodes.KEY_F1
    f2 = evdev.ecodes.KEY_F2
    f3 = evdev.ecodes.KEY_F3
    f4 = evdev.ecodes.KEY_F4
    f5 = evdev.ecodes.KEY_F5
    f6 = evdev.ecodes.KEY_F6
    f7 = evdev.ecodes.KEY_F7
    f8 = evdev.ecodes.KEY_F8
    f9 = evdev.ecodes.KEY_F9
    f10 = evdev.ecodes.KEY_F10
    f11 = evdev.ecodes.KEY_F11
    f12 = evdev.ecodes.KEY_F12
    f13 = evdev.ecodes.KEY_F13
    f14 = evdev.ecodes.KEY_F14
    f15 = evdev.ecodes.KEY_F15
    f16 = evdev.ecodes.KEY_F16
    f17 = evdev.ecodes.KEY_F17
    f18 = evdev.ecodes.KEY_F18
    f19 = evdev.ecodes.KEY_F19
    f20 = evdev.ecodes.KEY_F20
    home = evdev.ecodes.KEY_HOME
    left = evdev.ecodes.KEY_LEFT
    page_down = evdev.ecodes.KEY_PAGEDOWN
    page_up = evdev.ecodes.KEY_PAGEUP
    right = evdev.ecodes.KEY_RIGHT
    shift = evdev.ecodes.KEY_LEFTSHIFT
    shift_l = evdev.ecodes.KEY_LEFTSHIFT
    shift_r = evdev.ecodes.KEY_RIGHTSHIFT
    space = evdev.ecodes.KEY_SPACE
    tab = evdev.ecodes.KEY_TAB
    up = evdev.ecodes.KEY_UP

    media_play_pause = evdev.ecodes.KEY_PLAYPAUSE
    media_volume_mute = evdev.ecodes.KEY_MUTE
    media_volume_down = evdev.ecodes.KEY_VOLUMEDOWN
    media_volume_up = evdev.ecodes.KEY_VOLUMEUP
    media_previous = evdev.ecodes.KEY_PREVIOUSSONG
    media_next = evdev.ecodes.KEY_NEXTSONG

    insert = evdev.ecodes.KEY_INSERT
    menu = evdev.ecodes.KEY_MENU
    num_lock = evdev.ecodes.KEY_NUMLOCK
    pause = evdev.ecodes.KEY_PAUSE
    print_screen = evdev.ecodes.KEY_SYSRQ
    scroll_lock = evdev.ecodes.KEY_SCROLLLOCK


class KeyboardListener(threading.Thread):
    """
    UInput keyboard listener backend. Forwards EV_KEY events from grabbed
    physical keyboards to a virtual UInput device.
    Args:
        suppress: if True, do not forward events to UInput
    """

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
        self._ui = make_uinput(self._devices) if not suppress else None
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

    def map_key(self, key_code):
        # Firstr try to map in Key enum, then fallback to KeyCode
        try:
            return Key(key_code)
        except ValueError:
            # Get ecodes.KEY_* name for the code
            key_name = ecodes.KEY[key_code] if key_code in ecodes.KEY else None
            return KeyCode.from_vk(key_code, char=key_name)

    async def _forward(self, dev):
        async for event in dev.async_read_loop():
            # Print event class name
            if event.type == ecodes.EV_KEY:
                key_code = event.code
                pressed = event.value == 1
                key = self.map_key(key_code)
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
