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
Does an emulated absolute motion put the *logical* pointer where we asked?

mutter#5051 freezes the sprite, so eyes can't answer and gnome-shell Eval needs
unsafe mode. InputCapture can: the Activated signal reports the compositor's
own cursor position at the barrier. So for each target (x, y):

1. absolute-move the EIS pointer to (x, y);
2. push relatively *down* until the bottom barrier fires -> activation x must
   equal target x;
3. release, absolute-move to (x, y) again, push *right* until the right
   barrier fires -> activation y must equal target y.

Relative step counts are recorded too (pixels pushed vs distance to the edge),
which tells whether emulated relative motion is accelerated.

    python scripts/probe_abs_truth.py
    python scripts/probe_abs_truth.py --json out.json

Falsifier: ``--skip-abs`` skips step 1 (the absolute move). The activation
coordinates must then stop matching the targets; if they still match, the
cursor was already there and the check proved nothing.
"""

import argparse
import asyncio
import gc
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_ic_emulated as ic  # noqa: E402
import probe_rd_persistence as rd  # noqa: E402

PUSH = {"bottom": (0, 1), "right": (1, 0)}


async def push_until_activation(cap, sender, rel, edge, step, max_px):
    """Relative-push toward ``edge``; return (activation cursor, px pushed)."""
    dx, dy = PUSH[edge]
    act0 = cap.activation_id
    pushed = 0
    while pushed < max_px:
        rel.pointer_motion(dx * step, dy * step).frame()
        sender.dispatch()
        pushed += step
        await asyncio.sleep(0.02)
        if cap.activation_id != act0:
            return cap.cursor_position, pushed
    return None, pushed


async def run(args) -> dict:
    from snegg.ei import DeviceCapability

    out: dict = {"argv": sys.argv[1:], "desktop": os.environ.get("XDG_CURRENT_DESKTOP")}
    cap, receiver, poller, zones, bmap = ic.open_capture(args.timeout)
    out["zones"] = zones
    bus, rdp, session, sender, devices, report = await ic.open_emulation(
        args.token_file
    )
    out["ei_devices"] = report
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
    if absd is None:
        out["error"] = "no absolute device"
        return out

    w, h, zx, zy = zones[0]
    targets = [
        (zx + w // 2, zy + h // 2),
        (zx + 100, zy + 100),
        (zx + w - 300, zy + 200),
        (zx + 250, zy + h - 300),
        (zx + w // 3, zy + h // 3),
    ]
    results = []
    for tx, ty in targets:
        row = {"target": [tx, ty]}
        for edge in ("bottom", "right"):
            if not args.skip_abs:
                absd.pointer_motion_absolute(tx, ty).frame()
                sender.dispatch()
            await asyncio.sleep(0.15)
            ic.drain_receiver(receiver, poller)
            pos, pushed = await push_until_activation(
                cap, sender, rel, edge, args.step, max(w, h) * 2
            )
            if pos is None:
                row[edge] = {"activated": False, "pushed_px": pushed}
                continue
            ax, ay = pos
            if edge == "bottom":
                err, dist = ax - tx, (zy + h) - ty
            else:
                err, dist = ay - ty, (zx + w) - tx
            row[edge] = {
                "activation": [ax, ay],
                "error_px": err,
                "pushed_px": pushed,
                "distance_px": dist,
            }
            # Release well inside the zone so the next absolute move starts clean.
            cap.release(zx + w / 2, zy + h / 2)
            await asyncio.sleep(0.15)
            ic.drain_receiver(receiver, poller)
        b, r = row.get("bottom", {}), row.get("right", {})
        print(
            f"target=({tx},{ty})  x_err={b.get('error_px')}  y_err={r.get('error_px')}  "
            f"pushed/dist bottom={b.get('pushed_px')}/{b.get('distance_px')} "
            f"right={r.get('pushed_px')}/{r.get('distance_px')}"
        )
        results.append(row)
    out["results"] = results
    errs = [
        abs(row[e]["error_px"])
        for row in results
        for e in ("bottom", "right")
        if "error_px" in row.get(e, {})
    ]
    out["max_abs_error_px"] = max(errs) if errs else None
    out["all_activated"] = len(errs) == 2 * len(targets)
    print(
        f"activated {len(errs)}/{2 * len(targets)}, max |error| = {out['max_abs_error_px']} px"
    )

    del sender, rel, absd, devices
    gc.collect()
    cap.close()
    await rdp.call(
        "Close", "", [], iface="org.freedesktop.portal.Session", path=session
    )
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--step", type=int, default=4, help="px per relative push")
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument(
        "--skip-abs", action="store_true", help="falsifier: no absolute move"
    )
    p.add_argument("--token-file", type=Path, default=rd.DEFAULT_TOKEN_FILE)
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
