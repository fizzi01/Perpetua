import threading
import contextlib
import asyncio
import evdev
import enum
import inspect
from evdev import UInput, ecodes
from Xlib import display

def find_mice() -> list[evdev.InputDevice]:
    """Return all /dev/input/eventX devices that look like mice."""
    result = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            #print(f"Found device: {dev.path} ({dev.name}) with capabilities: {caps}")
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
    all_events = {}
    for dev in mice:
        caps = dev.capabilities()
        for etype, codes in caps.items():
            if etype not in all_events:
                all_events[etype] = set()
            all_events[etype].update(codes)
    # Only keep mouse-relevant event types
    filtered = {etype: sorted(codes) for etype, codes in all_events.items() if etype in (ecodes.EV_KEY, ecodes.EV_REL, ecodes.EV_MSC)}
    return UInput(
        name="perpetua-test-mouse",
        events=filtered,
    )

def _wrap(f, args):
    """Wraps a callable to make it accept ``args`` number of arguments.

    :param f: The callable to wrap. If this is ``None`` a no-op wrapper is
        returned.

    :param int args: The number of arguments to accept.

    :raises ValueError: if f requires more than ``args`` arguments
    """
    if f is None:
        return lambda *a: None
    else:
        argspec = inspect.getfullargspec(f)
        actual = len(inspect.signature(f).parameters)
        defaults = len(argspec.defaults) if argspec.defaults else 0
        if actual - defaults > args:
            raise ValueError(f)
        elif actual >= args or argspec.varargs is not None:
            return f
        else:
            return lambda *a: f(*a[:actual])


class X11Error(Exception):
    """An error that is thrown at the end of a code block managed by a
    :func:`display_manager` if an *X11* error occurred.
    """
    pass

Button = enum.Enum(
    'Button',
    module=__name__,
    names=[
        ('unknown', None),
        ('left', 1),
        ('middle', 2),
        ('right', 3)])

RawButtonMap = {
    ecodes.BTN_LEFT: Button.left,
    ecodes.BTN_MIDDLE: Button.middle,
    ecodes.BTN_RIGHT: Button.right
}
    

@contextlib.contextmanager
def display_manager(display):
    """Traps *X* errors and raises an :class:``X11Error`` at the end if any
    error occurred.

    This handler also ensures that the :class:`Xlib.display.Display` being
    managed is sync'd.

    :param Xlib.display.Display display: The *X* display.

    :return: the display
    :rtype: Xlib.display.Display
    """
    errors = []

    def handler(*args):
        """The *Xlib* error handler.
        """
        errors.append(args)

    old_handler = display.set_error_handler(handler)
    try:
        yield display
        display.sync()
    finally:
        display.set_error_handler(old_handler)
    if errors:
        raise X11Error(errors)


class MouseListener(threading.Thread):
    """
    Uinput mouse listener backend.
    Args:
        on_move: callable(x, y, injected)
        on_click: callable(x, y, button, pressed, injected)
        on_scroll: callable(x, y, dx, dy, injected)
        suppress: if True, do not forward events to UInput
    """
    def __init__(self, on_move=None, on_click=None, on_scroll=None, suppress=False):
        super().__init__(daemon=True)
        self.on_move = _wrap(on_move, 3)
        self.on_click = _wrap(on_click, 5)
        self.on_scroll = _wrap(on_scroll, 5)
        self._suppress = suppress
        self._running = threading.Event()
        self._running.clear()
        self._devices = find_mice()
        self._ui = make_uinput(self._devices) if not suppress else None
        self._display = None
        self._injected_flag = False
        self._injected_rel_count = 0
        self._injected_key_count = 0
        self._loop = None
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
        self._running.set()
        self._display = display.Display()
        try:
            for dev in self._devices:
                dev.grab()
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            tasks = [self._forward(dev) for dev in self._devices]
            self._loop.run_until_complete(asyncio.gather(*tasks))
        except asyncio.CancelledError:
            pass
        except RuntimeError as e:
            if "Event loop is closed" in str(e):
                pass  # Expected on shutdown
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

    async def _forward(self, dev):
        async for event in dev.async_read_loop():
            #print(f"[{dev.path}] {ecodes.EV.get(event.type, event.type)} {event.code} {event.value}")
            # Check for injected marker
            if event.type == ecodes.EV_MSC and event.code == ecodes.MSC_SCAN and event.value == 0x1337:
                self._injected_flag = True
                self._injected_rel_count = 2
                self._injected_key_count = 1
                continue
            injected = self._injected_flag
            # Handle injected counters
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
                pos = []
                if event.code == ecodes.REL_X:
                    pos.append(event.value)
                    pos.append(0)
                elif event.code == ecodes.REL_Y:
                    pos.append(0)
                    pos.append(event.value)
                if self.on_move:
                    pos = self._get_position()
                    if self.on_move(pos[0], pos[1], injected) is False:
                        self.stop()
                        break
            elif event.type == ecodes.EV_KEY:
                if event.code in RawButtonMap:
                    pressed = event.value == 1
                    if self.on_click:
                        pos = self._get_position()
                        if self.on_click(pos[0], pos[1], RawButtonMap[event.code], pressed, injected) is False:
                            self.stop()
                            break
            elif event.type == ecodes.EV_REL and event.code in (ecodes.REL_WHEEL, ecodes.REL_HWHEEL, ecodes.REL_WHEEL_HI_RES):
                dx = event.value if event.code == ecodes.REL_HWHEEL else 0
                dy = event.value if event.code == ecodes.REL_WHEEL else 0
                if self.on_scroll:
                    pos = self._get_position()
                    if self.on_scroll(pos[0], pos[1], dx, dy, injected) is False:
                        self.stop()
                        break
            if not self._suppress and self._ui:
                self._ui.write_event(event)
                self._ui.syn()
            if not self._running.is_set():
                break

    def start(self):
        super().start()

    def stop(self):
        self._running.clear()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def join(self, timeout=5):
        #self.stop()
        self._cleanup_done.wait(timeout)
        
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, value, traceback):
        self.stop()
        self.join()