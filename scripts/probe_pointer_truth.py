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
Can a Wayland client *read* its real pointer position while we inject motion?

Two candidate sources, same measurements:

``--source screencast``
    A ScreenCast stream added to the RemoteDesktop session with
    ``cursor_mode=METADATA``; mutter attaches the cursor position to the
    PipeWire buffers. ``probe_pw_cursor`` (C) reads them. Same restore token as
    the RD grant, so the consent stays one-off.

``--source shell``
    A GNOME Shell extension (``probe_shell_ext``) exposing
    ``global.get_pointer()`` on the session bus.

Measurements, in order:

1. ``still``     - no input for 1 s: the reading must not move.
2. ``absolute``  - absolute-move to 5 targets: reading vs target.
3. ``relative``  - relative steps from the centre: reading vs the sum of deltas,
                   then a push past the right edge: the reading must stop there.
4. ``latency``   - time from a 3 px relative move to the first changed reading.
5. ``physical``  - falsifier: you move the physical mouse while nothing is
                   injected; the reading must follow. An echo of our own
                   commands would stay put.

    python scripts/probe_pointer_truth.py --source screencast --json out.json
    python scripts/probe_pointer_truth.py --source shell --json out.json
"""

import argparse
import asyncio
import gc
import json
import secrets
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_rd_persistence as rd  # noqa: E402

HERE = Path(__file__).resolve().parent
SC_IFACE = "org.freedesktop.portal.ScreenCast"
SC_SOURCE_MONITOR = 1
SC_CURSOR_METADATA = 4
SC_TOKEN_FILE = Path.home() / ".cache" / "perpetua-probe" / "rd_sc_token.json"
SHELL_BUS = "org.perpetua.ProbePointer"
SHELL_PATH = "/org/perpetua/ProbePointer"


class Reading:
    """Latest pointer reading, written by whichever source runs."""

    def __init__(self):
        self.lock = threading.Lock()
        self.t = 0.0
        self.pos: tuple[float, float] | None = None
        self.updates = 0

    def set(self, t: float, x: float, y: float) -> None:
        with self.lock:
            self.t, self.pos = t, (x, y)
            self.updates += 1

    def get(self):
        with self.lock:
            return self.t, self.pos, self.updates


async def wait_change(reading: Reading, before, timeout: float = 1.0):
    """First reading different from ``before``, or None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, pos, _ = reading.get()
        if pos is not None and pos != before:
            return pos
        await asyncio.sleep(0.001)
    return None


async def settle(reading: Reading, quiet: float = 0.15, timeout: float = 1.5):
    """Reading once it stopped changing for ``quiet`` seconds."""
    deadline = time.monotonic() + timeout
    last, since = None, time.monotonic()
    while time.monotonic() < deadline:
        _, pos, _ = reading.get()
        if pos != last:
            last, since = pos, time.monotonic()
        elif time.monotonic() - since >= quiet:
            return last
        await asyncio.sleep(0.005)
    return last


# --- sources -----------------------------------------------------------------


async def open_session(
    bus, with_screencast: bool, token_file: Path, multiple: bool = False
):
    """RemoteDesktop (pointer) session, optionally with a cursor-metadata cast."""
    from dbus_fast import Variant

    portal = rd.Portal(bus)
    token = rd.load_token(token_file)
    print(f"RemoteDesktop (saved token: {'yes' if token else 'no'}, {token_file})")
    code, res, _ = await portal.request(
        "CreateSession",
        "a{sv}",
        [],
        {"session_handle_token": Variant("s", "perpetua_t" + secrets.token_hex(4))},
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
    if with_screencast:
        code, _, _ = await screencast_request(
            portal,
            "SelectSources",
            "oa{sv}",
            [session],
            {
                "types": Variant("u", SC_SOURCE_MONITOR),
                "cursor_mode": Variant("u", SC_CURSOR_METADATA),
                "multiple": Variant("b", multiple),
            },
        )
        if code:
            raise RuntimeError(f"SelectSources: {rd.RESPONSE_TEXT.get(code, code)}")
    print("Start... (answer the dialog if one appears)")
    code, res, latency = await portal.request("Start", "osa{sv}", [session, ""], {})
    if code:
        raise RuntimeError(f"Start: {rd.RESPONSE_TEXT.get(code, code)}")
    print(f"Start: granted in {latency:.2f}s")
    if res.get("restore_token"):
        rd.save_token(token_file, res["restore_token"], None)
    return portal, session, res


async def screencast_request(portal, member, signature, body, options):
    """``Portal.request`` against the ScreenCast interface (it defaults to RD)."""
    original = portal.call

    async def call(m, sig, b, **kw):
        if m == member:
            kw.setdefault("iface", SC_IFACE)
        return await original(m, sig, b, **kw)

    portal.call = call
    try:
        return await portal.request(member, signature, body, options)
    finally:
        portal.call = original


class ScreencastSource:
    def __init__(self, reading: Reading):
        self.reading = reading
        self.proc = None
        self.offset = (0, 0)
        self.stream_info = None

    async def start(self, bus, portal, session, start_results):
        streams = start_results.get("streams") or []
        if not streams:
            raise RuntimeError("Start returned no streams (cast not granted?)")
        # a(ua{sv}) arrives as a list of tuples, which _unwrap doesn't descend.
        node_id, props = rd._unwrap(streams[0][0]), rd._unwrap(dict(streams[0][1]))
        self.stream_info = {"node_id": node_id, **props}
        self.offset = tuple(props.get("position", (0, 0)))
        print(f"stream node={node_id} props={props}")
        reply = await portal.call(
            "OpenPipeWireRemote", "oa{sv}", [session, {}], iface=SC_IFACE
        )
        idx, fds = reply.body[0], reply.unix_fds or []
        fd = fds[idx] if isinstance(idx, int) and idx < len(fds) else idx
        candidates = (
            HERE / "probe_pw_cursor",
            Path.home() / ".cache" / "perpetua-probe" / "probe_pw_cursor",
        )
        helper = next((c for c in candidates if c.exists()), None)
        if helper is None:
            raise RuntimeError("build probe_pw_cursor first (run_probes.sh build-pw)")
        self.proc = subprocess.Popen(
            [str(helper), str(fd), str(node_id)],
            pass_fds=(fd,),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        ox, oy = self.offset
        for line in self.proc.stdout:
            parts = line.split()
            if len(parts) == 4 and parts[3] != "1":
                continue  # cursor off this stream
            if len(parts) not in (3, 4):
                continue
            t_us, x, y = parts[:3]
            self.reading.set(int(t_us) / 1e6, int(x) + ox, int(y) + oy)

    def stop(self) -> str:
        if self.proc is None:
            return ""
        self.proc.terminate()
        try:
            _, err = self.proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            _, err = self.proc.communicate()
        return err


class ShellSource:
    def __init__(self, reading: Reading):
        self.reading = reading
        self.task = None
        self.call_us: list[float] = []

    async def start(self, bus):
        from dbus_fast import Message, MessageType

        msg = lambda: Message(  # noqa: E731
            destination=SHELL_BUS,
            path=SHELL_PATH,
            interface=SHELL_BUS,
            member="GetPointer",
        )
        reply = await bus.call(msg())
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(
                f"{reply.error_name}: install and enable probe_shell_ext first"
            )

        async def poll():
            while True:
                t0 = time.monotonic()
                r = await bus.call(msg())
                t1 = time.monotonic()
                if r.message_type != MessageType.ERROR:
                    self.call_us.append((t1 - t0) * 1e6)
                    self.reading.set(t1, float(r.body[0]), float(r.body[1]))
                await asyncio.sleep(0.001)

        self.task = asyncio.get_running_loop().create_task(poll())

    def stop(self) -> str:
        if self.task:
            self.task.cancel()
        if not self.call_us:
            return ""
        return (
            f"GetPointer round trip: median {statistics.median(self.call_us):.0f} us, "
            f"max {max(self.call_us):.0f} us over {len(self.call_us)} calls"
        )


# --- measurements --------------------------------------------------------------


async def m_still(reading):
    p0 = await settle(reading)
    _, _, n0 = reading.get()
    await asyncio.sleep(1.0)
    _, p1, n1 = reading.get()
    return {"before": p0, "after": p1, "updates": n1 - n0, "moved": p0 != p1}


async def m_absolute(reading, sender, absd, zone):
    w, h, zx, zy = zone
    targets = [
        (zx + w // 2, zy + h // 2),
        (zx + 100, zy + 100),
        (zx + w - 300, zy + 200),
        (zx + 250, zy + h - 300),
        (zx + w // 3, zy + h // 3),
    ]
    rows = []
    for tx, ty in targets:
        absd.pointer_motion_absolute(tx, ty).frame()
        sender.dispatch()
        pos = await settle(reading)
        err = None if pos is None else (pos[0] - tx, pos[1] - ty)
        rows.append({"target": [tx, ty], "read": pos, "error": err})
        print(f"  abs target=({tx},{ty}) read={pos} error={err}")
    return rows


async def m_relative(reading, sender, rel, absd, zone):
    w, h, zx, zy = zone
    start = (zx + w // 2, zy + h // 2)
    absd.pointer_motion_absolute(*start).frame()
    sender.dispatch()
    pos = await settle(reading)
    expected = list(pos) if pos else list(start)
    worst = 0.0
    for _ in range(20):
        rel.pointer_motion(10, 5).frame()
        sender.dispatch()
        expected[0] += 10
        expected[1] += 5
        pos = await settle(reading, quiet=0.05)
        if pos:
            worst = max(worst, abs(pos[0] - expected[0]), abs(pos[1] - expected[1]))
    inside = {"expected": expected, "read": pos, "worst_error_px": worst}
    print(f"  rel 20x(+10,+5): expected={expected} read={pos} worst={worst}")

    edge_x = zx + w - 1
    for _ in range(40):
        rel.pointer_motion(20, 0).frame()
        sender.dispatch()
        await asyncio.sleep(0.01)
    pos = await settle(reading)
    clamp = {"edge_x": edge_x, "read": pos, "clamped": bool(pos and pos[0] <= edge_x)}
    print(f"  push past right edge: read={pos} edge_x={edge_x}")
    return {"inside": inside, "clamp": clamp}


async def m_latency(reading, sender, rel, absd, zone):
    w, h, zx, zy = zone
    absd.pointer_motion_absolute(zx + w // 2, zy + h // 2).frame()
    sender.dispatch()
    await settle(reading)
    lat, missed = [], 0
    for i in range(30):
        _, before, _ = reading.get()
        d = 3 if i % 2 == 0 else -3
        t0 = time.monotonic()
        rel.pointer_motion(d, 0).frame()
        sender.dispatch()
        after = await wait_change(reading, before, timeout=0.5)
        if after is None:
            missed += 1
        else:
            lat.append((time.monotonic() - t0) * 1000)
        await asyncio.sleep(0.03)
    out = {"samples": len(lat), "missed": missed}
    if lat:
        out.update(
            median_ms=round(statistics.median(lat), 2),
            max_ms=round(max(lat), 2),
        )
    print(f"  latency: {out}")
    return out


async def m_physical(reading):
    print(
        "\n  >>> Move the PHYSICAL mouse around inside the VM for 4 seconds "
        "(UTM input capture ON). Nothing is injected now."
    )
    input("      Enter to start... ")
    _, p0, n0 = reading.get()
    seen = {p0}
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        _, p, _ = reading.get()
        seen.add(p)
        await asyncio.sleep(0.01)
    _, _, n1 = reading.get()
    out = {"distinct_positions": len(seen), "updates": n1 - n0}
    out["follows_physical"] = out["distinct_positions"] > 5
    print(f"  physical: {out}")
    return out


async def run(args) -> dict:
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus
    from snegg.ei import DeviceCapability

    out: dict = {"argv": sys.argv[1:], "source": args.source}
    bus = await MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True).connect()
    reading = Reading()
    token_file = args.token_file or (
        SC_TOKEN_FILE if args.source == "screencast" else rd.DEFAULT_TOKEN_FILE
    )

    portal, session, start_res = await open_session(
        bus, args.source == "screencast", token_file
    )

    reply = await portal.call("ConnectToEIS", "oa{sv}", [session, {}])
    idx, fds = reply.body[0], reply.unix_fds or []
    fd = fds[idx] if isinstance(idx, int) and idx < len(fds) else idx
    sender, devices, report, err = rd.collect_devices(fd)
    out["ei_devices"] = report
    if err:
        out["error"] = err
        return out
    rel = next(
        d for d in devices.values() if DeviceCapability.POINTER in d.capabilities
    )
    absd = next(
        (
            d
            for d in devices.values()
            if DeviceCapability.POINTER_ABSOLUTE in d.capabilities
        ),
        None,
    )
    regions = next((d["regions"] for d in report if d["regions"]), [])
    if absd is None or not regions:
        out["error"] = "no absolute device with regions"
        return out
    r0 = regions[0]
    zone = (r0["w"], r0["h"], r0["x"], r0["y"])
    out["zone"] = zone

    source = (
        ScreencastSource(reading)
        if args.source == "screencast"
        else ShellSource(reading)
    )
    if args.source == "screencast":
        await source.start(bus, portal, session, start_res)
        out["stream"] = source.stream_info
    else:
        await source.start(bus)

    # Prime: a reading only exists after the first buffer / call.
    absd.pointer_motion_absolute(zone[2] + 10, zone[3] + 10).frame()
    sender.dispatch()
    first = await wait_change(reading, None, timeout=3.0)
    out["first_reading"] = first
    print(f"first reading: {first}")
    if first is None:
        out["error"] = "no reading at all"
        out["source_log"] = source.stop()
        return out

    try:
        input(
            "\n  >>> Hands off: move the Mac pointer OUTSIDE the VM window and turn\n"
            "      UTM input capture OFF until step 5 (UTM feeds the host pointer to\n"
            "      the guest as an absolute tablet). Enter to start... "
        )
        print("1. still")
        out["still"] = await m_still(reading)
        print(f"  {out['still']}")
        if out["still"]["moved"]:
            out["contaminated"] = True
            print(
                "  !!! the pointer moved with nothing injected: something else is "
                "driving it, steps 2-4 are not trustworthy"
            )
        print("2. absolute")
        out["absolute"] = await m_absolute(reading, sender, absd, zone)
        print("3. relative")
        out["relative"] = await m_relative(reading, sender, rel, absd, zone)
        print("4. latency")
        out["latency"] = await m_latency(reading, sender, rel, absd, zone)
        if not args.skip_physical:
            print("5. physical (falsifier)")
            out["physical"] = await m_physical(reading)
    finally:
        out["source_log"] = source.stop()
        print(out["source_log"])

    del sender, devices, rel, absd
    gc.collect()
    await portal.call(
        "Close", "", [], iface="org.freedesktop.portal.Session", path=session
    )
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--source", choices=("screencast", "shell"), required=True)
    p.add_argument("--token-file", type=Path)
    p.add_argument("--skip-physical", action="store_true")
    p.add_argument("--json", type=Path)
    args = p.parse_args()
    t0 = time.monotonic()
    out = asyncio.run(run(args))
    out["elapsed_s"] = round(time.monotonic() - t0, 1)
    if args.json:
        args.json.write_text(json.dumps(out, indent=2, default=str))
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
