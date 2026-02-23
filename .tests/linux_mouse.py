"""
Pure evdev mouse passthrough test.

Flow:
  real mouse  --[grab]--> this script --> UInput virtual device --> X server

The grab() call gives us exclusive access to the physical device so no
events leak to X; we then re-inject them through UInput.

Run with:  sudo .venv/bin/python .tests/linux_mouse.py
"""

# ---------------------------------------------------------------------------
# pynput-like MouseListener and MouseController
# ---------------------------------------------------------------------------
import threading
import time
import contextlib

import asyncio
import signal
import evdev
from evdev import UInput, ecodes
from Xlib import display

# Set to True to only print events without forwarding them to UInput
BLOCK_MOUSE = False


class MouseListener(threading.Thread):
    """
    pynput-like mouse listener using evdev.
    Args:
        on_move: callable(x, y, injected)
        on_click: callable(x, y, button, pressed, injected)
        on_scroll: callable(x, y, dx, dy, injected)
        suppress: if True, do not forward events to UInput
    """
    def __init__(self, on_move=None, on_click=None, on_scroll=None, suppress=False):
        super().__init__(daemon=True)
        self.on_move = on_move
        self.on_click = on_click
        self.on_scroll = on_scroll
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
                btn_map = {ecodes.BTN_LEFT: 'left', ecodes.BTN_RIGHT: 'right', ecodes.BTN_MIDDLE: 'middle'}
                if event.code in btn_map:
                    pressed = event.value == 1
                    pos = self._get_position()
                    if self.on_click:
                        if self.on_click(pos[0], pos[1], btn_map[event.code], pressed, injected) is False:
                            self.stop()
                            break
            elif event.type == ecodes.EV_REL and event.code in (ecodes.REL_WHEEL, ecodes.REL_HWHEEL, ecodes.REL_WHEEL_HI_RES):
                dx = event.value if event.code == ecodes.REL_HWHEEL else 0
                dy = event.value if event.code == ecodes.REL_WHEEL else 0
                pos = self._get_position()
                if self.on_scroll:
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


class X11Error(Exception):
    """An error that is thrown at the end of a code block managed by a
    :func:`display_manager` if an *X11* error occurred.
    """
    pass


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


class MouseController:
    """
    pynput-like mouse controller using UInput.
    """
    def __init__(self):
        self._ui = make_uinput(find_mice())
        self._pos = [0, 0]
        self._display = display.Display()

    @property
    def position(self):
        # Try to get real position from X11
        try:
            with display_manager(self._display) as d:
                root = d.screen().root
                pointer = root.query_pointer()
                return (pointer.root_x, pointer.root_y)
        except Exception:
            # fallback: local position
            return tuple(self._pos)

    @position.setter
    def position(self, pos):
        current = self.position
        dx = pos[0] - current[0]
        dy = pos[1] - current[1]
        self.move(dx, dy)

    def move(self, dx, dy):
        self._ui.write(ecodes.EV_MSC, ecodes.MSC_SCAN, 0x1337)
        self._ui.write(ecodes.EV_REL, ecodes.REL_X, dx)
        self._ui.write(ecodes.EV_REL, ecodes.REL_Y, dy)
        self._ui.syn()
        self._pos[0] += dx
        self._pos[1] += dy

    def press(self, button):
        btn_code = self._btn_to_code(button)
        self._ui.write(ecodes.EV_MSC, ecodes.MSC_SCAN, 0x1337)
        self._ui.write(ecodes.EV_KEY, btn_code, 1)
        self._ui.syn()

    def release(self, button):
        btn_code = self._btn_to_code(button)
        self._ui.write(ecodes.EV_MSC, ecodes.MSC_SCAN, 0x1337)
        self._ui.write(ecodes.EV_KEY, btn_code, 0)
        self._ui.syn()

    def click(self, button, count=1):
        for _ in range(count):
            self.press(button)
            time.sleep(0.01)
            self.release(button)

    def scroll(self, dx, dy):
        self._ui.write(ecodes.EV_MSC, ecodes.MSC_SCAN, 0x1337)
        if dx:
            self._ui.write(ecodes.EV_REL, ecodes.REL_HWHEEL, dx)
        if dy:
            self._ui.write(ecodes.EV_REL, ecodes.REL_WHEEL, dy)
        self._ui.syn()

    def _btn_to_code(self, button):
        if button == 'left':
            return ecodes.BTN_LEFT
        elif button == 'right':
            return ecodes.BTN_RIGHT
        elif button == 'middle':
            return ecodes.BTN_MIDDLE
        raise ValueError(f"Unknown button: {button}")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_mice() -> list[evdev.InputDevice]:
    """Return all /dev/input/eventX devices that look like mice."""
    result = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            print(f"Found device: {dev.path} ({dev.name}) with capabilities: {caps}")
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

# ---------------------------------------------------------------------------
# Async event loop
# ---------------------------------------------------------------------------

async def forward(dev: evdev.InputDevice, ui: UInput) -> None:
    """Read events from one grabbed device and emit them on UInput."""
    async for event in dev.async_read_loop():
        # Print all events for debug
        if event.type in (ecodes.EV_KEY, ecodes.EV_REL, ecodes.EV_MSC):
            type_name = ecodes.EV[event.type]
            code_name = ecodes.REL.get(event.code) or ecodes.KEY.get(event.code) or event.code
            print(f"[{dev.path}] {type_name} {code_name} {event.value}")
        if not BLOCK_MOUSE:
            ui.write_event(event)
            ui.syn()


async def main() -> None:
    mice = find_mice()
    if not mice:
        print("[ERROR] No mouse devices found (try running with sudo)")
        return

    ui = make_uinput(mice)
    print(f"Virtual device: {ui.device.path}")

    for dev in mice:
        print(f"Grabbing: {dev.path}  ({dev.name})")
        dev.grab()

    loop = asyncio.get_event_loop()

    def _shutdown():
        for dev in mice:
            try:
                dev.ungrab()
            except Exception:
                pass
        ui.close()
        loop.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    tasks = [asyncio.ensure_future(forward(dev, ui)) for dev in mice]
    await asyncio.gather(*tasks, return_exceptions=True)

async def test():
    #controller = MouseController()
    def on_move(x, y, injected):
        print(f"Move: {x}, {y} (injected={injected})")

    def on_click(x, y, button, pressed, injected):
        print(f"Click: {button} {'pressed' if pressed else 'released'} at {x}, {y} (injected={injected})")

    def on_scroll(x, y, dx, dy, injected):
        print(f"Scroll: dx={dx}, dy={dy} at {x}, {y} (injected={injected})")

    # Listen for ctrl+c to stop the listener
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()
    def signal_handler():
        stop_event.set()
    loop.add_signal_handler(signal.SIGINT, signal_handler)

    with MouseListener(on_move=on_move, on_click=on_click, on_scroll=on_scroll) as listener:
        print("Mouse listener started. Move the mouse or click buttons to see events. Press Ctrl+C to stop.")
        await stop_event.wait()
        print("Stopping mouse listener...")
        listener.stop()



if __name__ == "__main__":
    asyncio.run(test())
