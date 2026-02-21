"""
Pure evdev keyboard passthrough test.

Flow:
  real keyboard  --[grab]--> this script --> UInput virtual device --> X server

The grab() call gives us exclusive access to the physical device so no
events leak to X; we then re-inject them through UInput.

Run with:  sudo .venv/bin/python .tests/linux_input.py
"""

import asyncio
import signal
import evdev
from evdev import UInput, ecodes

KEY_MAX = 767  # kernel KEY_MAX; codes above this are rejected by uinput

# Set to True to only print events without forwarding them to UInput
BLOCK_KEYS = False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_keyboards() -> list[evdev.InputDevice]:
    """Return all /dev/input/eventX devices that have alphanumeric keys."""
    result = []
    for path in evdev.list_devices():
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
    """Create a UInput device that mirrors the union of all keyboard caps."""
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


# ---------------------------------------------------------------------------
# Async event loop
# ---------------------------------------------------------------------------

async def forward(dev: evdev.InputDevice, ui: UInput) -> None:
    """Read events from one grabbed device and emit them on UInput."""
    async for event in dev.async_read_loop():
        if event.type == ecodes.EV_KEY:
            value_str = {1: "press", 0: "release", 2: "repeat"}.get(event.value, str(event.value))
            key_name = ecodes.KEY.get(event.code, event.code)
            print(f"[{dev.path}] {key_name} {value_str}")
            if not BLOCK_KEYS:
                ui.write(ecodes.EV_KEY, event.code, event.value)
                ui.syn()

async def main() -> None:
    keyboards = find_keyboards()
    if not keyboards:
        print("[ERROR] No keyboard devices found (try running with sudo)")
        return

    ui = make_uinput(keyboards)
    print(f"Virtual device: {ui.device.path}")

    for dev in keyboards:
        print(f"Grabbing: {dev.path}  ({dev.name})")
        dev.grab()

    loop = asyncio.get_event_loop()

    def _shutdown():
        for dev in keyboards:
            try:
                dev.ungrab()
            except Exception:
                pass
        ui.close()
        loop.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    tasks = [asyncio.ensure_future(forward(dev, ui)) for dev in keyboards]
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
