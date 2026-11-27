"""
Whether the InputCapture permission dialog comes back - measured, per delay.

After the user cancels the "allow input capture" dialog, the daemon's retries
stop producing a dialog at all: the portal accepts the ``CreateSession`` and
then never answers it (45 s of silence), while disabling and re-enabling the
server service *does* bring the dialog back. The only difference between those
two paths is how much time passed, so the question this probe answers is:

    how long after an answered (or abandoned) request will GNOME show the
    next dialog?

It drives ``pyinputcapture.InputCapturePortal`` directly - the same
``setup(edges, timeout=...)`` call ``_CaptureSession._setup`` makes - with no
daemon, no service layer, no config, and deliberately **no fd-2 suppression**,
so the extension's own messages (which the capture thread sends to /dev/null)
are visible here.

    python scripts/probe_input_capture.py                       # 0,0,2,5,10,20 s
    python scripts/probe_input_capture.py --delays 0,0,0         # back-to-back only
    python scripts/probe_input_capture.py --reuse                # one portal object
    python scripts/probe_input_capture.py --keep-objects         # fresh, none dropped
    python scripts/probe_input_capture.py --timeout 20           # patient operator
    python scripts/probe_input_capture.py --json out.json        # raw attempts

Measured answer to the question above: **no delay recovers it, and the delay was
never the variable.** With a fresh portal object per attempt, everything after
the first answered request was silent - 0 s, 10 s, 30 s, 60 s alike - while
``--keep-objects`` (nothing dropped) was answered every single time. The cause
was inside this process:

``ashpd`` caches the D-Bus session connection in a process-global static
(``ashpd/src/proxy.rs:27``), but ``InputCapturePortal::new()`` used to build
*its own* tokio runtime per object, and the cached connection's zbus tasks live
on whichever runtime created it first. ``Drop`` then called
``rt.shutdown_background()``, killing those tasks while the dead connection
stayed in the static - after which every request was written to a socket nobody
reads. That is what "no reply, ever, whatever the delay" looks like.

pyinputcapture 0.3.0 shares one runtime across the whole process, which is the
fix. This script stays as the regression test for it: run it **without**
``--keep-objects``, cancel the first dialog, and every later attempt must still
show one. ``--keep-objects`` remains the falsifier - it was answered even on the
broken build, so a run where only it works means the runtime is per-object
again.

**Answer every dialog you see** - cancelling is fine, and cancelling is the
point for the first one. An answered request proves the dialog appeared; an
attempt that runs out its timeout proves it did not. Watch
``journalctl --user -u xdg-desktop-portal-gnome -f`` alongside: a dialog that
opened behind a window is indistinguishable from no dialog at all from here,
and the portal's log tells the two apart.
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
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

#: Delays between one attempt's outcome and the next attempt, in seconds. The
#: first is 0 because the first attempt has nothing to wait for; the second is 0
#: as well, and that is the measurement the daemon's bug is made of - a request
#: issued in the same millisecond the previous one was cancelled.
DEFAULT_DELAYS = (0.0, 0.0, 2.0, 5.0, 10.0, 20.0)

#: Per-attempt wait for an answer. Short on purpose: the daemon uses 45 s, which
#: is a long time to learn "no dialog appeared" and is why nobody noticed the
#: retries were silent. Long enough that an operator can find and click a dialog.
DEFAULT_TIMEOUT = 15.0

#: What the daemon arms. Kept identical so the request is the same one.
DEFAULT_EDGES = ("bottom", "left", "right", "top")

#: Outcomes, in the order the verdict reasons about them.
ANSWERED = "answered"  # dialog appeared and was answered (session or refusal)
SILENT = "silent"  # no answer within the timeout: no dialog reached the user
BROKEN = "error"  # something else failed


def _classifiers():
    """The daemon's own reason-string matchers, if this box can import them.

    Falls back to local copies: the probe must run where ``snegg``/``evdev`` are
    missing too, and the strings are the extension's, not the daemon's.
    """
    try:
        from input.mouse.backend import _libei

        return _libei._is_permission_failure, _libei._is_setup_timeout
    except Exception as exc:
        print(f"  (using local matchers: {type(exc).__name__}: {exc})")
        hints = ("not authorized", "not authorised", "denied", "cancel", "didn't succe")
        return (
            lambda reason: any(h in reason.lower() for h in hints),
            lambda reason: "portal setup timed out" in reason.lower(),
        )


def _attempt(portal_cls, portal, edges, timeout, is_refusal, is_timeout):
    """One ``setup()``, classified. Returns (record, portal_or_None)."""
    if portal is None:
        portal = portal_cls()

    started = time.monotonic()
    try:
        zones, eis_fd, barrier_map = portal.setup(list(edges), timeout=timeout)
    except Exception as exc:
        elapsed = time.monotonic() - started
        reason = str(exc)
        if is_timeout(reason):
            outcome = SILENT
        elif is_refusal(reason):
            outcome = ANSWERED
        else:
            outcome = BROKEN
        return (
            {"outcome": outcome, "elapsed": elapsed, "reason": reason},
            portal,
        )

    elapsed = time.monotonic() - started
    # A live session holds the barriers; hand it straight back so the next
    # attempt starts from the same state the daemon's retries do.
    try:
        os.close(eis_fd)
    except OSError:
        pass
    try:
        portal.close()
    except Exception as exc:
        print(f"  (close after a granted session failed: {exc})")
    return (
        {
            "outcome": ANSWERED,
            "elapsed": elapsed,
            "reason": None,
            "granted": True,
            "zones": zones,
            "barriers": len(barrier_map),
        },
        portal,
    )


def _verdict(report):
    """What the attempts say about the retry cadence the daemon needs."""
    attempts = report["attempts"]
    print("\nVERDICT")

    silent = [a for a in attempts if a["outcome"] == SILENT]
    answered = [a for a in attempts if a["outcome"] == ANSWERED]

    if not silent:
        print(
            "  Every attempt reached the user, including the ones issued with no\n"
            "  delay. The dialog is not being suppressed by timing - look for a\n"
            "  second portal consumer, or for a dialog opening off-screen."
        )
    elif not answered:
        print(
            "  No attempt was ever answered. Either no dialog is being shown at\n"
            "  all on this box, or they open somewhere the operator cannot see\n"
            "  them - check the xdg-desktop-portal-gnome log before reading any\n"
            "  cadence out of this run."
        )
    else:
        # The smallest delay that still produced a dialog is the floor the
        # daemon has to respect; anything shorter is a request thrown away.
        recovered = min(a["delay"] for a in answered[1:]) if len(answered) > 1 else None
        worst_silent = max(a["delay"] for a in silent)
        print(f"  Silent at delays: {sorted({a['delay'] for a in silent})}")
        print(f"  Answered at delays: {sorted({a['delay'] for a in answered})}")
        if recovered is None:
            print(
                "  Only the first attempt was ever answered: after one dialog the\n"
                "  portal stopped showing them for the whole run. A delay alone\n"
                "  does not recover it - the abandoned request has to be closed."
            )
        else:
            print(
                f"  Smallest delay that still produced a dialog: {recovered:.0f}s\n"
                f"  Largest delay that did not: {worst_silent:.0f}s\n"
                f"  => _MIN_ATTEMPT_GAP should be >= {max(recovered, worst_silent):.0f}s"
            )

    if any(a["outcome"] == BROKEN for a in attempts):
        print("  Some attempts failed for unrelated reasons; read their reason lines.")


def main():
    parser = argparse.ArgumentParser(
        description="Measure when the InputCapture permission dialog comes back."
    )
    parser.add_argument(
        "--delays",
        default=",".join(str(d) for d in DEFAULT_DELAYS),
        help="comma-separated seconds to wait before each attempt",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="keep one portal object across attempts instead of a fresh one",
    )
    parser.add_argument(
        "--keep-objects",
        action="store_true",
        help=(
            "fresh portal object per attempt, but never drop the old ones - "
            "isolates 'the D-Bus connection is still alive' from 'the same "
            "object was used' (see the module docstring)"
        ),
    )
    parser.add_argument("--json", help="write the raw attempts here")
    args = parser.parse_args()

    try:
        delays = [float(d) for d in args.delays.split(",") if d.strip()]
    except ValueError:
        parser.error(f"--delays must be numbers: {args.delays!r}")
    if not delays:
        parser.error("--delays must list at least one attempt")

    try:
        from pyinputcapture import InputCapturePortal
    except Exception as exc:
        print(f"pyinputcapture is not importable here: {type(exc).__name__}: {exc}")
        return 2

    is_refusal, is_timeout = _classifiers()

    print(
        f"\n{len(delays)} attempts, {args.timeout:.0f}s for an answer each, "
        f"{'one portal object' if args.reuse else 'a fresh portal object per attempt'}."
    )
    print(
        "Answer every dialog you see - cancel is fine, and cancel is the point\n"
        "for the first one. An attempt that runs out its timeout means no dialog\n"
        "reached you.\n"
    )
    print(f"  {'#':>2}  {'delay':>6}  {'outcome':<9} {'elapsed':>8}  reason")

    report = {
        "delays": delays,
        "timeout": args.timeout,
        "reuse": args.reuse,
        "keep_objects": args.keep_objects,
        "edges": list(DEFAULT_EDGES),
        "attempts": [],
    }
    portal = None
    # Only ever appended to: the point is that nothing in here is ever dropped,
    # so the tokio runtime that owns ashpd's process-global D-Bus connection
    # (``static SESSION: OnceLock<zbus::Connection>``) stays alive.
    kept: list = []

    for index, delay in enumerate(delays, start=1):
        if delay:
            for remaining in range(int(delay), 0, -1):
                print(f"  waiting {remaining}s...", end="\r", flush=True)
                time.sleep(1)
            time.sleep(delay - int(delay))
            print(" " * 30, end="\r")

        record, portal = _attempt(
            InputCapturePortal,
            portal if args.reuse else None,
            DEFAULT_EDGES,
            args.timeout,
            is_refusal,
            is_timeout,
        )
        record["attempt"] = index
        record["delay"] = delay
        report["attempts"].append(record)

        reason = record.get("reason") or ("granted" if record.get("granted") else "")
        print(
            f"  {index:>2}  {delay:>5.0f}s  {record['outcome']:<9} "
            f"{record['elapsed']:>7.1f}s  {reason}"
        )

        if args.keep_objects and portal is not None:
            kept.append(portal)

        if record.get("granted"):
            # Permission granted is the terminal state: there is nothing left to
            # measure, because every later attempt is answered from the stored
            # grant without a dialog. Running on regardless is what looked like
            # the script hanging after the operator clicked "allow".
            print(
                "\n  Permission granted - stopping here: with the grant in place "
                "no further attempt raises a dialog, so there is nothing left to "
                "measure. Re-run after revoking it to measure the refusal path."
            )
            break
        # A granted-then-closed object cannot be set up again ("already set
        # up"), so it is never carried forward even with --reuse. Otherwise:
        # dropped here rather than at the next iteration, so whatever its
        # abandoned task holds is released before the delay rather than during
        # the next request - unless --keep-objects parked a reference above,
        # which is the whole experiment.
        if record.get("granted") or not args.reuse:
            portal = None

    _verdict(report)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nraw attempts written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
