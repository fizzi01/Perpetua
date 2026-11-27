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
Does an InputCapture barrier fire for a cursor moved by our own libei emulation?

The client-side-barrier design (lan-mouse's: the controlled machine detects the
return to the server with its *own* InputCapture barrier) rests on one
assumption nobody guarantees in code: the compositor activates a barrier when
the pointer reaching it is driven by a RemoteDesktop/EIS device of the **same
machine**, not by a physical mouse. This probe answers exactly that:

1. opens an InputCapture session with barriers on all four edges
   (``pyinputcapture``, the same ``setup()`` the server uses) - dialog 1;
2. opens a RemoteDesktop session and its EIS sender - dialog 2, unless
   ``probe_rd_persistence.py`` already saved a token (it reuses it);
3. drives the emulated pointer relatively toward ``--edge`` and watches
   ``portal.activation_id``.

It also records what happens *after* activation: whether our own emulated
motion then shows up on the capture receiver (a feedback loop the design must
break) and where the portal says the cursor was.

    pip install dbus-fast
    python scripts/probe_ic_emulated.py                  # push right
    python scripts/probe_ic_emulated.py --edge left --step 30
    python scripts/probe_ic_emulated.py --physical       # falsifier, see below
    python scripts/probe_ic_emulated.py --json out.json

Falsifier: ``--physical`` skips the emulation and asks you to push the real
mouse into the edge. It **must** activate. If it doesn't, barriers are broken
on this session and a negative emulated result means nothing.
"""

import argparse
import asyncio
import gc
import json
import os
import select
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_rd_persistence as rd  # noqa: E402

EDGE_DIR = {"right": (1, 0), "left": (-1, 0), "bottom": (0, 1), "top": (0, -1)}


def open_capture(timeout: float):
    from pyinputcapture import InputCapturePortal
    from snegg.ei import DeviceCapability, EventType, Receiver

    portal = InputCapturePortal()
    print("InputCapture setup... (answer the dialog)")
    zones, eis_fd, bmap = portal.setup(
        ["left", "right", "top", "bottom"], timeout=timeout
    )
    receiver = Receiver.create_for_fd(
        os.fdopen(eis_fd, "rb", closefd=False), name="perpetua-probe-capture"
    )
    poller = select.poll()
    poller.register(receiver.fd, select.POLLIN)
    caps = (
        DeviceCapability.POINTER,
        DeviceCapability.POINTER_ABSOLUTE,
        DeviceCapability.BUTTON,
        DeviceCapability.SCROLL,
    )
    deadline = time.monotonic() + 5
    bound = False
    while not bound and time.monotonic() < deadline:
        if poller.poll(100):
            receiver.dispatch()
        for ev in receiver.events:
            if rd.event_type(ev) == EventType.SEAT_ADDED:
                ev.seat.bind(caps)
                bound = True
    portal.enable()
    print(f"  zones={zones} barriers={bmap}")
    return portal, receiver, poller, zones, dict(bmap)


def drain_receiver(receiver, poller) -> list[str]:
    seen = []
    while poller.poll(0):
        receiver.dispatch()
        seen.extend(
            getattr(rd.event_type(ev), "name", "UNKNOWN") for ev in receiver.events
        )
    return seen


async def open_emulation(token_file: Path):
    from dbus_fast import BusType, Variant
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True).connect()
    portal = rd.Portal(bus)
    token = rd.load_token(token_file)
    print(f"RemoteDesktop (saved token: {'yes' if token else 'no'})...")
    code, res, _ = await portal.request(
        "CreateSession",
        "a{sv}",
        [],
        {"session_handle_token": Variant("s", "perpetua_ic")},
    )
    if code:
        raise RuntimeError(f"CreateSession: {rd.RESPONSE_TEXT.get(code, code)}")
    session = res["session_handle"]
    sel = {
        "types": Variant("u", rd.DEVICE_POINTER),
        "persist_mode": Variant("u", rd.PERSIST_UNTIL_REVOKED),
    }
    if token:
        sel["restore_token"] = Variant("s", token)
    code, _, _ = await portal.request("SelectDevices", "oa{sv}", [session], sel)
    if code:
        raise RuntimeError(f"SelectDevices: {rd.RESPONSE_TEXT.get(code, code)}")
    code, res, _ = await portal.request("Start", "osa{sv}", [session, ""], {})
    if code:
        raise RuntimeError(f"Start: {rd.RESPONSE_TEXT.get(code, code)}")
    if res.get("restore_token"):
        rd.save_token(token_file, res["restore_token"], None)
    reply = await portal.call("ConnectToEIS", "oa{sv}", [session, {}])
    idx, fds = reply.body[0], reply.unix_fds or []
    fd = fds[idx] if isinstance(idx, int) and idx < len(fds) else idx
    sender, devices, report, err = rd.collect_devices(fd)
    if err:
        raise RuntimeError(err)
    return bus, portal, session, sender, devices, report


async def run(args) -> dict:
    from snegg.ei import DeviceCapability

    out: dict = {
        "argv": sys.argv[1:],
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP"),
        "edge": args.edge,
    }
    cap_portal, receiver, poller, zones, bmap = open_capture(args.timeout)
    out["zones"], out["barriers"] = zones, {str(k): v for k, v in bmap.items()}
    act0 = cap_portal.activation_id

    sender = rel = devices = absd = None
    if not args.physical:
        bus, rdp, session, sender, devices, report = await open_emulation(
            args.token_file
        )
        out["ei_devices"] = report
        rel = next(
            (d for d in devices.values() if DeviceCapability.POINTER in d.capabilities),
            None,
        )
        absd = next(
            (
                d
                for d in devices.values()
                if DeviceCapability.POINTER_ABSOLUTE in d.capabilities
            ),
            None,
        )
        if rel is None:
            out["error"] = "no relative pointer device"
            return out
        if absd is not None and zones:
            w, h, x, y = zones[0]
            absd.pointer_motion_absolute(x + w / 2, y + h / 2).frame()
            sender.dispatch()
            await asyncio.sleep(0.3)
        drain_receiver(receiver, poller)

    dx, dy = EDGE_DIR[args.edge]
    activated_at = None
    t0 = time.monotonic()
    if args.physical:
        print(f"push the PHYSICAL mouse into the {args.edge} edge (30 s)...")
    for step in range(args.steps):
        if not args.physical:
            rel.pointer_motion(dx * args.step, dy * args.step).frame()
            sender.dispatch()
        await asyncio.sleep(args.interval if not args.physical else 0.1)
        if cap_portal.activation_id != act0:
            activated_at = step
            break
        if args.physical and time.monotonic() - t0 > 30:
            break

    out["activated"] = activated_at is not None
    if activated_at is None:
        print(f"NO activation after {args.steps} steps of {args.step}px")
    else:
        out.update(
            activated_step=activated_at,
            activation_id=cap_portal.activation_id,
            barrier_id=cap_portal.barrier_id,
            barrier_edge=bmap.get(cap_portal.barrier_id),
            activation_cursor=cap_portal.cursor_position,
        )
        print(
            f"ACTIVATED at step {activated_at}: barrier={out['barrier_edge']} "
            f"cursor={out['activation_cursor']}"
        )
        # While captured: does our own emulated motion loop back to the receiver?
        drain_receiver(receiver, poller)
        if not args.physical:
            for _ in range(10):
                rel.pointer_motion(dx * args.step, dy * args.step).frame()
                sender.dispatch()
                await asyncio.sleep(args.interval)
        await asyncio.sleep(0.2)
        seen = drain_receiver(receiver, poller)
        out["receiver_events_while_captured"] = seen
        print(f"receiver events while captured: {sorted(set(seen)) or 'none'}")
        cx, cy = cap_portal.cursor_position
        back = {
            "right": (cx - 2, cy),
            "left": (cx + 2, cy),
            "bottom": (cx, cy - 2),
            "top": (cx, cy + 2),
        }[args.edge]
        cap_portal.release(*back)

    del sender, rel, devices, absd
    gc.collect()  # free libei before Close; see probe_rd_persistence
    cap_portal.close()
    if not args.physical:
        await rdp.call(
            "Close", "", [], iface="org.freedesktop.portal.Session", path=session
        )
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--edge", choices=sorted(EDGE_DIR), default="right")
    p.add_argument("--step", type=int, default=40, help="px per emulated step")
    p.add_argument("--steps", type=int, default=200)
    p.add_argument("--interval", type=float, default=0.02)
    p.add_argument(
        "--timeout", type=float, default=120.0, help="InputCapture dialog wait"
    )
    p.add_argument(
        "--physical", action="store_true", help="falsifier: use the real mouse"
    )
    p.add_argument("--token-file", type=Path, default=rd.DEFAULT_TOKEN_FILE)
    p.add_argument("--json", type=Path)
    args = p.parse_args()
    out = asyncio.run(run(args))
    if args.json:
        args.json.write_text(json.dumps(out, indent=2, default=str))
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
