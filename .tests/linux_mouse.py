"""
Pure evdev mouse passthrough test.

Flow:
  real mouse  --[grab]--> this script --> UInput virtual device --> X server

The grab() call gives us exclusive access to the physical device so no
events leak to X; we then re-inject them through UInput.

Run with:  sudo .venv/bin/python .tests/linux_mouse.py
"""

import asyncio
import signal
import evdev
from evdev import UInput, ecodes

# Set to True to only print events without forwarding them to UInput
BLOCK_MOUSE = False

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


if __name__ == "__main__":
    asyncio.run(main())
