"""
What the OS does with injected motion at a screen edge - measured, per platform.

The client decides "the user is pushing back out to the server" from the motion
the server forwards while the cursor sits on the entry edge. Every design for
that decision rests on assumptions about what the OS reports in exactly that
state, and those assumptions differ per platform in ways that are invisible
from the code. This probe measures them instead of assuming them.

It drives the **real** ``ClientMouseController`` for the current platform -
its ``_cursor_position``, ``_warp_cursor`` and ``_inject_relative``, the same
three primitives the client uses - so what is measured is what runs. Nothing
else about the daemon is started, no event is dispatched, no config is touched;
the only side effect is the cursor moving, and the position it started from is
restored on exit.

    python scripts/probe_edge_travel.py                  # everything, main monitor
    python scripts/probe_edge_travel.py --edge top       # one edge
    python scripts/probe_edge_travel.py --monitor 2      # a specific monitor
    python scripts/probe_edge_travel.py --geometry       # print layout, move nothing
    python scripts/probe_edge_travel.py --json out.json  # raw samples for comparison

Run it on macOS **and** on Windows and compare the two reports: the questions
in the VERDICT block are the ones the return-to-server design has to answer,
and they are the ones the two platforms are expected to answer differently.
"""


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

import argparse
import atexit
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from input.mouse import ClientMouseController  # noqa: E402
from utils.screen import Screen  # noqa: E402

#: Inter-tick delay. The server forwards moves at ~125 Hz, and the cadence
#: matters: the macOS backend measures the applied displacement against the
#: position it read at the *previous* injection, so a probe that sleeps longer
#: than the real stream measures a different thing.
DEFAULT_INTERVAL_MS = 8.0

#: Ticks per push. Long enough that a 12 px margin (``RETURN_PUSH_MARGIN``)
#: would be crossed several times over if the deltas were credited.
DEFAULT_TICKS = 24

#: Per-tick delta, in the same order of magnitude as a real hand movement.
DEFAULT_STEP = 6

#: How long a warp is given to take effect before anything is measured.
WARP_SETTLE_S = 0.15

EDGES = ("top", "bottom", "left", "right")

#: Outward unit vector per edge, in screen coordinates.
_OUTWARD = {
    "top": (0, -1),
    "bottom": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

#: Axis each edge constrains: the perpendicular one is the only component that
#: can express "in or out" through that edge.
_PERP_AXIS = {"top": "y", "bottom": "y", "left": "x", "right": "x"}


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


class _Stub:
    """Stand-in for the event bus / stream handlers the controller wants.

    The constructor wires callbacks onto them, so they have to absorb
    arbitrary calls. Every name touched is recorded and printed at the end:
    the probe drives the cursor primitives directly and should never reach the
    networking or event paths, so anything beyond the constructor's own wiring
    appearing in that list means the probe is measuring more than it claims to.
    """

    touched: set = set()

    def __getattr__(self, name):
        _Stub.touched.add(name)
        return lambda *a, **kw: None


def _controller() -> ClientMouseController:
    return ClientMouseController(
        event_bus=_Stub(), stream_handler=_Stub(), command_stream=_Stub()
    )


def _monitors():
    layout = Screen.get_monitor_layout()
    return list(layout.monitors), layout


def _pick_monitor(monitors, wanted):
    if not monitors:
        raise SystemExit("no monitors reported by Screen.get_monitor_layout()")
    if wanted is None:
        return monitors[0]
    for m in monitors:
        if m.monitor_id == wanted:
            return m
    ids = ", ".join(str(m.monitor_id) for m in monitors)
    raise SystemExit(f"no monitor with id {wanted} (have: {ids})")


def _edge_point(monitor, edge):
    """A point exactly ON *edge*, centred along it - where a landing puts it."""
    mid_x = (monitor.min_x + monitor.max_x) // 2
    mid_y = (monitor.min_y + monitor.max_y) // 2
    return {
        "top": (mid_x, monitor.min_y),
        "bottom": (mid_x, monitor.max_y - 1),
        "left": (monitor.min_x, mid_y),
        "right": (monitor.max_x - 1, mid_y),
    }[edge]


def _axis_at_bound(monitor, x, y, dx, dy):
    """Per-axis: is the cursor already against the bound this delta pushes at?

    The same geometric question ``_motion_is_bounded`` asks, split per axis so
    the report can show which component the OS could not honour.
    """

    def one(pos, delta, low, high):
        if delta == 0:
            return None
        return (delta < 0 and pos <= low) or (delta > 0 and pos >= high - 1)

    return (
        one(x, dx, monitor.min_x, monitor.max_x),
        one(y, dy, monitor.min_y, monitor.max_y),
    )


# ---------------------------------------------------------------------------
# the one measurement everything else is built from
# ---------------------------------------------------------------------------


def _push(ctl, monitor, dx, dy, ticks, interval_s, label):
    """Inject *ticks* deltas of ``(dx, dy)``, recording what each one did.

    Per tick it captures the two signals the return gate is built on, plus the
    OS truth to check them against:

    - ``at_bound`` - the geometric answer to "can the cursor still go that
      way", read off the position before the injection. This is what
      ``_check_edge`` decides from.
    - ``requested`` - what the server asked for. This is what
      ``_accumulate_outward_push`` credits.
    - ``observed`` - what the cursor actually did, measured by the probe from
      the position read-back. The OS's own answer, and the one neither of the
      above is allowed to contradict.

    ``_inject_relative`` deliberately reports nothing (see its docstring), so
    there is no fourth column any more - that missing column *was* the bug.
    """
    samples = []
    pos = ctl._cursor_position()
    for i in range(ticks):
        if pos is None:
            break
        x, y = pos
        bx, by = _axis_at_bound(monitor, x, y, dx, dy)
        bounded = ctl._motion_is_bounded(x, y, dx, dy)
        under = ctl._find_monitor_for_cursor(x, y)

        t0 = time.perf_counter()
        ctl._inject_relative(dx, dy)
        time.sleep(interval_s)
        nxt = ctl._cursor_position()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        observed = (
            (round(nxt[0] - x), round(nxt[1] - y)) if nxt is not None else (None, None)
        )
        samples.append(
            {
                "tick": i,
                "pos": [round(x), round(y)],
                "requested": [dx, dy],
                "observed": list(observed),
                "x_at_bound": bx,
                "y_at_bound": by,
                "motion_is_bounded": bounded,
                "monitor": getattr(under, "monitor_id", None),
                "tick_ms": round(elapsed_ms, 2),
            }
        )
        pos = nxt

    return {
        "label": label,
        "requested_per_tick": [dx, dy],
        "ticks": len(samples),
        "requested_total": [dx * len(samples), dy * len(samples)],
        "observed_total": [
            sum(s["observed"][0] or 0 for s in samples),
            sum(s["observed"][1] or 0 for s in samples),
        ],
        "start_pos": samples[0]["pos"] if samples else None,
        "end_pos": samples[-1]["pos"] if samples else None,
        "samples": samples,
    }


def _print_push(run, perp_axis):
    idx = 0 if perp_axis == "x" else 1
    req = run["requested_total"][idx]
    obs = run["observed_total"][idx]
    print(f"  {run['label']}")
    print(f"    perpendicular ({perp_axis}): requested {req:+5d}   observed {obs:+5d}")
    print(f"    position: {run['start_pos']} -> {run['end_pos']}")

    key = "x_at_bound" if perp_axis == "x" else "y_at_bound"
    frozen = [s for s in run["samples"] if s["observed"][idx] == 0]
    print(
        f"    ticks the cursor did not move on that axis: {len(frozen)}/{run['ticks']}"
    )

    # The invariant the design rests on: every tick the OS refused to move must
    # be one the geometry already predicted. A single exception here means the
    # gate can be fed by something other than a real edge.
    unexplained = [s for s in frozen if s[key] is not True]
    if unexplained:
        print(
            f"    !! {len(unexplained)} of them NOT predicted by the bound test "
            f"(first at pos={unexplained[0]['pos']})"
        )
    elif frozen:
        print("    all of them predicted by the bound test")

    # The converse: the geometry must not claim a bound the cursor walked past.
    lying = [
        s
        for s in run["samples"]
        if s[key] is True and s["observed"][idx] not in (0, None)
    ]
    if lying:
        print(
            f"    !! the bound test said 'blocked' on {len(lying)} ticks where "
            "the cursor moved anyway"
        )


# ---------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------


def probe_geometry(monitors, layout):
    print("== geometry ==")
    print(f"  virtual bbox: {layout.virtual_bbox}")
    for m in monitors:
        print(
            f"  monitor {m.monitor_id}: "
            f"x[{m.min_x}, {m.max_x}) y[{m.min_y}, {m.max_y})"
            f"  {m.max_x - m.min_x}x{m.max_y - m.min_y}"
        )
    return {
        "virtual_bbox": list(layout.virtual_bbox),
        "monitors": [
            {
                "id": m.monitor_id,
                "min_x": m.min_x,
                "min_y": m.min_y,
                "max_x": m.max_x,
                "max_y": m.max_y,
            }
            for m in monitors
        ],
    }


def probe_landing(ctl, monitor, edge):
    """Where does a landing warp actually put the cursor, and how fast?

    The gate has to recognise "on the entry edge". If a warp to the edge does
    not settle exactly on the coordinate the geometry predicts, no test against
    that coordinate can be exact.
    """
    target = _edge_point(monitor, edge)
    ctl._warp_cursor(*target)
    t0 = time.perf_counter()
    settled = None
    last = None
    deadline = t0 + WARP_SETTLE_S
    while time.perf_counter() < deadline:
        pos = ctl._cursor_position()
        if pos is not None:
            reached = (round(pos[0]), round(pos[1]))
            if settled is None and reached == target:
                settled = (time.perf_counter() - t0) * 1000.0
            last = reached
        time.sleep(0.002)

    print(f"  landing warp -> {target}")
    print(
        f"    settled at {last}"
        + (
            f" after {settled:.1f} ms"
            if settled is not None
            else "  !! never matched the requested point"
        )
    )
    return {
        "edge": edge,
        "requested": list(target),
        "settled_at": list(last) if last else None,
        "settle_ms": round(settled, 2) if settled is not None else None,
        "exact": last == target,
    }


def probe_edge(ctl, monitor, edge, ticks, step, interval_s):
    """The whole story for one edge, in the order the user lives it."""
    print(f"\n== edge {edge} (monitor {monitor.monitor_id}) ==")
    out = {"edge": edge, "monitor_id": monitor.monitor_id}

    out["landing"] = probe_landing(ctl, monitor, edge)

    ux, uy = _OUTWARD[edge]
    perp = _PERP_AXIS[edge]
    par_step = step if perp == "y" else 0
    par_step_y = step if perp == "x" else 0

    # 1. Straight outward from the landing: the exact thing a user does to
    #    hand control back, and the case the return gate exists for.
    out["outward"] = _push(
        ctl,
        monitor,
        ux * step,
        uy * step,
        ticks,
        interval_s,
        "straight out from the landing",
    )
    _print_push(out["outward"], perp)
    out["resting"] = ctl._cursor_position()
    print(f"    resting coordinate after the push: {out['resting']}")
    print(
        "    predicted by the geometry: "
        + str(_edge_point(monitor, edge))
        + ("  (match)" if out["resting"] == _edge_point(monitor, edge) else "")
    )

    # 2. The same push with motion along the edge as well. This is the shape a
    #    real hand produces, and the free axis moving is what makes a
    #    whole-vector "did anything move" test read healthy while the axis that
    #    matters is frozen.
    ctl._warp_cursor(*_edge_point(monitor, edge))
    time.sleep(WARP_SETTLE_S)
    out["diagonal"] = _push(
        ctl,
        monitor,
        ux * step + par_step,
        uy * step + par_step_y,
        ticks,
        interval_s,
        "outward + along the edge",
    )
    _print_push(out["diagonal"], perp)

    # 3. Control: inward from the edge. Nothing is blocking this, so a backend
    #    that reports honestly must report the full delta.
    ctl._warp_cursor(*_edge_point(monitor, edge))
    time.sleep(WARP_SETTLE_S)
    out["inward"] = _push(
        ctl,
        monitor,
        -ux * step,
        -uy * step,
        min(ticks, 8),
        interval_s,
        "inward from the landing (control)",
    )
    _print_push(out["inward"], perp)

    return out


def probe_readback_lag(ctl, monitor, interval_s):
    """How stale is the position read-back after an injection?

    A backend that measures the applied displacement is reading a value that
    lags its own injection. Whether that lag is one tick or ten decides whether
    a position-based test can be evaluated on the same tick as the delta that
    caused it.
    """
    print("\n== read-back lag ==")
    mid = ((monitor.min_x + monitor.max_x) // 2, (monitor.min_y + monitor.max_y) // 2)
    ctl._warp_cursor(*mid)
    time.sleep(WARP_SETTLE_S)

    lags = []
    for _ in range(5):
        before = ctl._cursor_position()
        t0 = time.perf_counter()
        ctl._inject_relative(10, 0)
        seen = None
        while time.perf_counter() - t0 < 0.1:
            pos = ctl._cursor_position()
            if pos is not None and before is not None and pos[0] != before[0]:
                seen = (time.perf_counter() - t0) * 1000.0
                break
            time.sleep(0.001)
        lags.append(round(seen, 2) if seen is not None else None)
        time.sleep(interval_s)

    print(f"  ms until the position reflected a 10 px injection: {lags}")
    return {"lags_ms": lags}


def probe_neighbour(ctl, monitors, monitor, ticks, step, interval_s):
    """Where does the cursor stop on an edge shared with another monitor?

    A geometric "at the bound" test uses the monitor under the cursor. With an
    adjacent monitor of a different height the desktop union is bigger than
    where the cursor can go, and part of the shared edge is a dead zone. This
    measures whether the cursor stops at the monitor bound, slips onto the
    neighbour, or stops somewhere else entirely.
    """
    if len(monitors) < 2:
        return None
    print("\n== neighbour edges ==")
    results = []
    for edge in EDGES:
        ux, uy = _OUTWARD[edge]
        others = [
            m
            for m in monitors
            if m.monitor_id != monitor.monitor_id
            and (
                (edge == "left" and m.max_x == monitor.min_x)
                or (edge == "right" and m.min_x == monitor.max_x)
                or (edge == "top" and m.max_y == monitor.min_y)
                or (edge == "bottom" and m.min_y == monitor.max_y)
            )
        ]
        if not others:
            continue
        ctl._warp_cursor(*_edge_point(monitor, edge))
        time.sleep(WARP_SETTLE_S)
        run = _push(
            ctl,
            monitor,
            ux * step,
            uy * step,
            ticks,
            interval_s,
            f"outward at {edge}, neighbour {[m.monitor_id for m in others]}",
        )
        end = run["end_pos"]
        under = ctl._find_monitor_for_cursor(*end) if end else None
        crossed = under is not None and under.monitor_id != monitor.monitor_id
        print(
            f"  {edge}: ended at {end} on monitor "
            f"{getattr(under, 'monitor_id', None)}"
            + ("  (crossed onto the neighbour)" if crossed else "  (held)")
        )
        results.append(
            {
                "edge": edge,
                "neighbours": [m.monitor_id for m in others],
                "end_pos": end,
                "ended_on_monitor": getattr(under, "monitor_id", None),
                "crossed": crossed,
                "run": run,
            }
        )
    return results


# ---------------------------------------------------------------------------
# verdict
# ---------------------------------------------------------------------------


def _verdict(report):
    """Check the invariants the return-to-server design rests on.

    Not a description of what happened - a pass/fail on the two properties the
    gate needs from the OS. Both must hold on every platform, or the gate
    cannot be written the same way on all of them.
    """
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    print(f"  platform: {sys.platform}")
    print(f"  controller: {report['controller']}")
    print(f"  daemon plumbing touched: {sorted(_Stub.touched) or 'none'}")

    ok = True
    for edge in report.get("edges", []):
        perp = _PERP_AXIS[edge["edge"]]
        idx = 0 if perp == "x" else 1
        key = "x_at_bound" if perp == "x" else "y_at_bound"
        out = edge["outward"]
        obs = out["observed_total"][idx]
        req = out["requested_total"][idx]
        landing = edge["landing"]

        print(f"\n  [{edge['edge']}] pushing outward from the landing:")
        print(f"    requested {req:+d}, the cursor really moved {obs:+d}")
        print(f"    resting coordinate while held: {edge.get('resting')}")

        # 1. A landing must be recognisable as "on the edge" straight away,
        #    or the first push is credited against the wrong state.
        if not landing["exact"]:
            ok = False
            print(
                f"    FAIL  the landing warp settled at {landing['settled_at']}, "
                f"not the predicted {landing['requested']}"
            )
        else:
            print("    ok    the landing warp lands exactly where predicted")

        # 2. Every tick the OS refused must be predicted by the bound test -
        #    that test is the only thing keeping the push accumulator honest.
        frozen = [s for s in out["samples"] if s["observed"][idx] == 0]
        unexplained = [s for s in frozen if s[key] is not True]
        if unexplained:
            ok = False
            print(
                f"    FAIL  {len(unexplained)}/{len(frozen)} frozen ticks were "
                "not predicted by the bound test"
            )
        else:
            print(
                f"    ok    all {len(frozen)} frozen ticks predicted by the bound test"
            )

    print(
        "\n  "
        + (
            "all invariants hold on this platform"
            if ok
            else "SOME INVARIANTS FAILED - the gate cannot rely on them here"
        )
    )
    print(
        "  Run this on the other platform too: what the design must not depend\n"
        "  on is anything the two reports disagree about."
    )
    return ok


# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Measure what the OS does with injected motion at a screen edge."
    )
    parser.add_argument("--edge", choices=EDGES, action="append", help="repeatable")
    parser.add_argument("--monitor", type=int, help="monitor id (default: the first)")
    parser.add_argument("--ticks", type=int, default=DEFAULT_TICKS)
    parser.add_argument("--step", type=int, default=DEFAULT_STEP)
    parser.add_argument("--interval-ms", type=float, default=DEFAULT_INTERVAL_MS)
    parser.add_argument(
        "--geometry", action="store_true", help="print the layout and exit"
    )
    parser.add_argument("--json", help="write the raw samples here")
    parser.add_argument(
        "--countdown", type=int, default=3, help="seconds before the cursor moves"
    )
    args = parser.parse_args()

    monitors, layout = _monitors()
    geometry = probe_geometry(monitors, layout)
    if args.geometry:
        return 0

    ctl = _controller()
    monitor = _pick_monitor(monitors, args.monitor)
    interval_s = args.interval_ms / 1000.0

    start = ctl._cursor_position()
    if start is not None:
        atexit.register(lambda: ctl._warp_cursor(*start))

    print(
        f"\nDriving {type(ctl).__module__}.{type(ctl).__name__} on monitor "
        f"{monitor.monitor_id}. The cursor will move; it is put back at the end."
    )
    for remaining in range(args.countdown, 0, -1):
        print(f"  starting in {remaining}...", end="\r", flush=True)
        time.sleep(1)
    print(" " * 40, end="\r")

    report = {
        "platform": sys.platform,
        "controller": f"{type(ctl).__module__}.{type(ctl).__name__}",
        "geometry": geometry,
        "monitor_id": monitor.monitor_id,
        "ticks": args.ticks,
        "step": args.step,
        "interval_ms": args.interval_ms,
        "edges": [],
    }

    for edge in args.edge or EDGES:
        report["edges"].append(
            probe_edge(ctl, monitor, edge, args.ticks, args.step, interval_s)
        )

    report["readback"] = probe_readback_lag(ctl, monitor, interval_s)
    report["neighbour"] = probe_neighbour(
        ctl, monitors, monitor, args.ticks, args.step, interval_s
    )

    _verdict(report)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nraw samples written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
