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
ScreenCast cursor metadata with several monitors (and scaling): which space?

One ScreenCast stream per monitor (``multiple=True``, select *every* monitor in
the dialog). For targets on each EI region the pointer is moved absolutely and
every stream's last meta is recorded, so the report shows:

- which stream reports the cursor as valid, and the others' values;
- raw meta x/y vs the target's offset inside that stream's ``position`` (the
  ratio says whether the meta is in logical or physical pixels);
- a relative walk across the boundary between the first two regions.

Then the CPU cost: helper processes and gnome-shell, idle vs moving.

    python scripts/probe_pointer_multi.py --json out.json

Falsifier: with one monitor (or one stream selected) the report must show a
single stream, and every target valid on it.
"""

import argparse
import asyncio
import gc
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_pointer_truth as pt  # noqa: E402
import probe_rd_persistence as rd  # noqa: E402

TOKEN_FILE = Path.home() / ".cache" / "perpetua-probe" / "rd_sc_multi_token.json"
CLK_TCK = os.sysconf("SC_CLK_TCK")


class StreamReader:
    """One helper per stream; keeps the latest meta, valid or not."""

    def __init__(self, index: int, node_id: int, props: dict):
        self.index = index
        self.node_id = node_id
        self.props = props
        self.lock = threading.Lock()
        self.last = None  # (t, x, y, valid)
        self.updates = 0
        self.proc = None

    def start(self, helper: Path, fd: int, max_fps: int):
        self.proc = subprocess.Popen(
            [str(helper), str(fd), str(self.node_id), str(max_fps)],
            pass_fds=(fd,),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            parts = line.split()
            if len(parts) != 4:
                continue
            with self.lock:
                self.last = (
                    int(parts[0]) / 1e6,
                    int(parts[1]),
                    int(parts[2]),
                    parts[3] == "1",
                )
                self.updates += 1

    def get(self):
        with self.lock:
            return self.last

    def stop(self) -> str:
        if self.proc is None:
            return ""
        self.proc.terminate()
        try:
            _, err = self.proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            _, err = self.proc.communicate()
        return err.strip()


def cpu_seconds(pid: int) -> float:
    with open(f"/proc/{pid}/stat") as f:
        fields = f.read().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / CLK_TCK  # utime + stime


def gnome_shell_pid() -> int | None:
    try:
        out = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-x", "gnome-shell"],
            capture_output=True,
            text=True,
        ).stdout.split()
        return int(out[0]) if out else None
    except Exception:
        return None


def snapshot(readers):
    return {r.index: r.get() for r in readers}


async def place(sender, absd, x, y, readers, wait=0.3):
    t0 = time.monotonic()
    absd.pointer_motion_absolute(x, y).frame()
    sender.dispatch()
    await asyncio.sleep(wait)
    row = {"target": [x, y], "streams": {}}
    for r in readers:
        last = r.get()
        if last is None:
            row["streams"][r.index] = None
            continue
        t, mx, my, valid = last
        px, py = r.props.get("position", (0, 0))
        entry = {"raw": [mx, my], "valid": valid, "fresh": t >= t0}
        if valid:
            off = (x - px, y - py)
            entry["target_offset_in_stream"] = list(off)
            entry["ratio"] = [
                round(mx / off[0], 3) if off[0] else None,
                round(my / off[1], 3) if off[1] else None,
            ]
        row["streams"][r.index] = entry
    return row


async def cpu_window(seconds, readers, shell_pid, mover=None):
    pids = {f"helper{r.index}": r.proc.pid for r in readers}
    if shell_pid:
        pids["gnome-shell"] = shell_pid
    before = {k: cpu_seconds(p) for k, p in pids.items()}
    updates0 = {r.index: r.updates for r in readers}
    t0 = time.monotonic()
    task = asyncio.get_running_loop().create_task(mover()) if mover else None
    await asyncio.sleep(seconds)
    if task:
        task.cancel()
    wall = time.monotonic() - t0
    out = {
        k: round(100 * (cpu_seconds(p) - before[k]) / wall, 1) for k, p in pids.items()
    }
    for r in readers:
        out[f"meta_per_s{r.index}"] = round((r.updates - updates0[r.index]) / wall, 1)
    return out


async def run(args) -> dict:
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus
    from snegg.ei import DeviceCapability

    out: dict = {"argv": sys.argv[1:]}
    bus = await MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True).connect()
    if args.baseline:
        return await run_baseline(bus, out)
    portal, session, start_res = await pt.open_session(
        bus, True, args.token_file, multiple=True
    )
    streams = start_res.get("streams") or []
    readers = []
    for i, (node_id, props) in enumerate(streams):
        props = rd._unwrap(dict(props))
        readers.append(StreamReader(i, rd._unwrap(node_id), props))
    out["streams"] = [{"node_id": r.node_id, **r.props} for r in readers]
    print(f"{len(readers)} stream(s):")
    for r in readers:
        print(f"  [{r.index}] node={r.node_id} {r.props}")
    if not readers:
        out["error"] = "no streams"
        return out

    helper = next(
        (
            c
            for c in (
                pt.HERE / "probe_pw_cursor",
                Path.home() / ".cache" / "perpetua-probe" / "probe_pw_cursor",
            )
            if c.exists()
        ),
        None,
    )
    if helper is None:
        out["error"] = "build probe_pw_cursor first (run_probes.sh build-pw)"
        return out
    for r in readers:
        reply = await portal.call(
            "OpenPipeWireRemote", "oa{sv}", [session, {}], iface=pt.SC_IFACE
        )
        idx, fds = reply.body[0], reply.unix_fds or []
        r.start(
            helper,
            fds[idx] if isinstance(idx, int) and idx < len(fds) else idx,
            args.max_fps,
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
    out["regions"] = regions
    print("EI regions:")
    for g in regions:
        print(f"  {g['w']}x{g['h']}+{g['x']}+{g['y']} scale={g['physical_scale']}")
    if absd is None or not regions:
        out["error"] = "no absolute device with regions"
        return out

    input(
        "\n  >>> Hands off: Mac pointer OUTSIDE the VM window, UTM input capture "
        "OFF. Enter to start... "
    )

    print("1. targets on every region")
    rows = []
    for g in regions:
        x, y, w, h = g["x"], g["y"], g["w"], g["h"]
        for tx, ty in (
            (x + w // 2, y + h // 2),
            (x + 40, y + 40),
            (x + w - 40, y + h - 40),
        ):
            row = await place(sender, absd, tx, ty, readers)
            row["region"] = [x, y, w, h]
            rows.append(row)
            valid = {k: v for k, v in row["streams"].items() if v and v["valid"]}
            print(
                f"  target=({tx},{ty}) valid on {list(valid)}: "
                + "; ".join(
                    f"[{k}] raw={v['raw']} ratio={v['ratio']}" for k, v in valid.items()
                )
            )
    out["targets"] = rows

    if len(regions) >= 2:
        print("2. relative walk across the first boundary")
        a, b = regions[0], regions[1]
        ax, ay = a["x"] + a["w"] // 2, a["y"] + a["h"] // 2
        bx, by = b["x"] + b["w"] // 2, b["y"] + b["h"] // 2
        steps = 60
        dx, dy = (bx - ax) / steps, (by - ay) / steps
        absd.pointer_motion_absolute(ax, ay).frame()
        sender.dispatch()
        await asyncio.sleep(0.3)
        walk = []
        for i in range(steps):
            rel.pointer_motion(dx, dy).frame()
            sender.dispatch()
            await asyncio.sleep(0.03)
            walk.append(
                {
                    "step": i,
                    "expected": [round(ax + dx * (i + 1)), round(ay + dy * (i + 1))],
                    "streams": {
                        k: (list(v[1:3]) + [v[3]]) if v else None
                        for k, v in snapshot(readers).items()
                    },
                }
            )
        out["walk"] = walk
        changes = [
            w
            for j, w in enumerate(walk)
            if j == 0
            or {k: v[2] if v else None for k, v in w["streams"].items()}
            != {k: v[2] if v else None for k, v in walk[j - 1]["streams"].items()}
        ]
        for w in changes:
            print(f"  step {w['step']} expected={w['expected']} streams={w['streams']}")

    print("3. CPU (% of one core), 5 s idle then 5 s moving")
    shell = gnome_shell_pid()
    g = regions[0]

    async def mover(absd=absd, sender=sender):
        i = 0
        while True:
            i += 1
            x = g["x"] + g["w"] // 2 + int(200 * ((i % 40) / 40 - 0.5))
            absd.pointer_motion_absolute(x, g["y"] + g["h"] // 2).frame()
            sender.dispatch()
            await asyncio.sleep(0.008)

    out["cpu_idle"] = await cpu_window(5, readers, shell)
    print(f"  idle:   {out['cpu_idle']}")
    out["cpu_moving"] = await cpu_window(5, readers, shell, mover)
    print(f"  moving: {out['cpu_moving']}")

    print("4. latency on stream 0 (3 px relative moves)")
    lat, missed = [], 0
    absd.pointer_motion_absolute(g["x"] + g["w"] // 2, g["y"] + g["h"] // 2).frame()
    sender.dispatch()
    await asyncio.sleep(0.3)
    for i in range(30):
        before = readers[0].get()
        t0 = time.monotonic()
        rel.pointer_motion(3 if i % 2 == 0 else -3, 0).frame()
        sender.dispatch()
        while time.monotonic() - t0 < 0.5:
            now = readers[0].get()
            if now is not None and now[1:3] != (before[1:3] if before else None):
                lat.append((time.monotonic() - t0) * 1000)
                break
            await asyncio.sleep(0.001)
        else:
            missed += 1
        await asyncio.sleep(0.03)
    out["latency"] = {"samples": len(lat), "missed": missed}
    if lat:
        lat.sort()
        out["latency"].update(
            median_ms=round(lat[len(lat) // 2], 2), max_ms=round(lat[-1], 2)
        )
    print(f"  {out['latency']}")

    out["helper_logs"] = {r.index: r.stop() for r in readers}
    for k, v in out["helper_logs"].items():
        print(f"  helper[{k}]: {v.splitlines()[-1] if v else ''}")

    del sender, devices, rel, absd, mover
    gc.collect()
    await portal.call(
        "Close", "", [], iface="org.freedesktop.portal.Session", path=session
    )
    return out


async def run_baseline(bus, out: dict) -> dict:
    """Same moving-pointer CPU window, RemoteDesktop only: the cost without a cast."""
    from snegg.ei import DeviceCapability

    portal, session, _ = await pt.open_session(bus, False, rd.DEFAULT_TOKEN_FILE)
    reply = await portal.call("ConnectToEIS", "oa{sv}", [session, {}])
    idx, fds = reply.body[0], reply.unix_fds or []
    fd = fds[idx] if isinstance(idx, int) and idx < len(fds) else idx
    sender, devices, report, err = rd.collect_devices(fd)
    if err:
        out["error"] = err
        return out
    absd = next(
        d
        for d in devices.values()
        if DeviceCapability.POINTER_ABSOLUTE in d.capabilities
    )
    g = next(d["regions"] for d in report if d["regions"])[0]
    input(
        "\n  >>> Hands off: Mac pointer OUTSIDE the VM window, UTM input capture "
        "OFF. Enter to start... "
    )

    async def mover(absd=absd, sender=sender):
        i = 0
        while True:
            i += 1
            x = g["x"] + g["w"] // 2 + int(200 * ((i % 40) / 40 - 0.5))
            absd.pointer_motion_absolute(x, g["y"] + g["h"] // 2).frame()
            sender.dispatch()
            await asyncio.sleep(0.008)

    shell = gnome_shell_pid()
    print("baseline CPU (% of one core), no ScreenCast: 5 s idle then 5 s moving")
    out["cpu_idle"] = await cpu_window(5, [], shell)
    print(f"  idle:   {out['cpu_idle']}")
    out["cpu_moving"] = await cpu_window(5, [], shell, mover)
    print(f"  moving: {out['cpu_moving']}")
    del sender, devices, absd, mover
    gc.collect()
    await portal.call(
        "Close", "", [], iface="org.freedesktop.portal.Session", path=session
    )
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    p.add_argument("--max-fps", type=int, default=1000, help="cap the video framerate")
    p.add_argument(
        "--baseline", action="store_true", help="CPU with RemoteDesktop only, no cast"
    )
    p.add_argument("--json", type=Path)
    args = p.parse_args()
    out = asyncio.run(run(args))
    if args.json:
        args.json.write_text(json.dumps(out, indent=2, default=str))
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
