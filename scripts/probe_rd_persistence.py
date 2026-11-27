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
What an input-only RemoteDesktop session gives us on this compositor - measured.

Three questions decide the Wayland client design, and none of them can be
answered by reading code:

1. **Persistence.** With ``persist_mode=2`` and a saved ``restore_token``, does
   ``Start`` come back *without* a dialog for a session that has no screencast
   (devices=POINTER only)? Under which ``app_id`` does the permission store
   record the grant?
2. **Absolute device.** Does the EIS connection obtained via ``ConnectToEIS``
   offer a ``POINTER_ABSOLUTE`` device with regions, and in which coordinate
   space are those regions? (The server-authoritative design needs it.)
3. **Positioning and sprite.** Does ``pointer_motion_absolute`` put the cursor
   exactly where asked, and does the cursor sprite follow (mutter#5051 was
   reported for *relative* motion)?

No daemon, no config: the portal is driven directly over D-Bus (``dbus-fast``)
and the fd handed to ``snegg.ei.Sender`` exactly as the backend would.

    pip install dbus-fast        # only for this probe, not a project dependency

    python scripts/probe_rd_persistence.py                    # run 1: dialog expected
    python scripts/probe_rd_persistence.py                    # run 2: must be silent
    python scripts/probe_rd_persistence.py --app-id com.federicoizzi.Perpetua
    python scripts/probe_rd_persistence.py --move             # positioning test
    python scripts/probe_rd_persistence.py --move --truth shell-eval
    python scripts/probe_rd_persistence.py --forget           # drop the saved token
    python scripts/probe_rd_persistence.py --json out.json

How to read it:

- ``start_latency_s`` with a token is the persistence answer. A restored session
  answers in well under a second; a dialog takes as long as the human. The run
  also asks "did a dialog appear?" on a tty, because latency alone cannot tell
  a fast click from a silent restore - answer honestly, the JSON keeps both.
- ``grant_app_ids`` is what the permission store keyed the grant under. ``""``
  means the portal could not identify us: tokens then survive only as long as
  that stays true, and they break the day the launch path changes.
- ``--truth shell-eval`` reads the real pointer through ``org.gnome.Shell.Eval``
  (``global.get_pointer()``). It only works under ``gnome-shell --unsafe-mode``
  (test VM only). Without it the positioning test asks you to look.

Falsifier: ``--forget`` then run again. The dialog **must** appear. A run that
"restores" with no token means the probe is measuring nothing.
"""

import argparse
import asyncio
import gc
import json
import os
import secrets
import select
import sys
import time
from pathlib import Path

try:
    from dbus_fast import BusType, Message, MessageType, Variant
    from dbus_fast.aio import MessageBus
except ImportError:  # pragma: no cover - probe only
    sys.exit("dbus-fast is required for this probe: pip install dbus-fast")

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
RD_IFACE = "org.freedesktop.portal.RemoteDesktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"
REGISTRY_IFACE = "org.freedesktop.host.portal.Registry"
PERM_BUS = "org.freedesktop.impl.portal.PermissionStore"
PERM_PATH = "/org/freedesktop/impl/portal/PermissionStore"

DEVICE_KEYBOARD = 1
DEVICE_POINTER = 2
PERSIST_UNTIL_REVOKED = 2

RESPONSE_TEXT = {0: "granted", 1: "cancelled by user", 2: "denied/ended"}

DEFAULT_TOKEN_FILE = Path.home() / ".cache" / "perpetua-probe" / "rd_token.json"


def _unwrap(value):
    if isinstance(value, Variant):
        return _unwrap(value.value)
    if isinstance(value, dict):
        return {k: _unwrap(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_unwrap(v) for v in value]
    return value


class Portal:
    """Minimal Request/Response driver for the RemoteDesktop portal."""

    def __init__(self, bus: MessageBus):
        self.bus = bus
        self._sender = bus.unique_name[1:].replace(".", "_")

    async def call(
        self,
        member,
        signature,
        body,
        *,
        iface=RD_IFACE,
        path=PORTAL_PATH,
        dest=PORTAL_BUS,
    ):
        reply = await self.bus.call(
            Message(
                destination=dest,
                path=path,
                interface=iface,
                member=member,
                signature=signature,
                body=body,
            )
        )
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"{member}: {reply.error_name}: {reply.body}")
        return reply

    async def request(self, member, signature, body_without_options, options):
        """Call a Request-returning method and wait for its Response signal.

        The match rule is installed *before* the call, on the predicted request
        path: subscribing afterwards races a portal that answers instantly
        (a restored session does).
        """
        token = "perpetua_" + secrets.token_hex(6)
        options = dict(options, handle_token=Variant("s", token))
        path = f"{PORTAL_PATH}/request/{self._sender}/{token}"
        loop = asyncio.get_running_loop()
        fut = loop.create_future()

        def handler(msg):
            if (
                msg.message_type == MessageType.SIGNAL
                and msg.path == path
                and msg.member == "Response"
                and not fut.done()
            ):
                fut.set_result((msg.body[0], _unwrap(msg.body[1])))

        rule = (
            f"type='signal',interface='{REQUEST_IFACE}',member='Response',path='{path}'"
        )
        await self.call(
            "AddMatch",
            "s",
            [rule],
            iface="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            dest="org.freedesktop.DBus",
        )
        self.bus.add_message_handler(handler)
        try:
            t0 = time.monotonic()
            await self.call(member, signature, [*body_without_options, options])
            code, results = await fut
            return code, results, time.monotonic() - t0
        finally:
            self.bus.remove_message_handler(handler)


async def get_version(portal: Portal) -> int | None:
    try:
        reply = await portal.call(
            "Get", "ss", [RD_IFACE, "version"], iface="org.freedesktop.DBus.Properties"
        )
        return int(_unwrap(reply.body[0]))
    except Exception:
        return None


async def register_app_id(portal: Portal, app_id: str) -> str:
    # Must be the very first portal call on this connection.
    try:
        await portal.call("Register", "sa{sv}", [app_id, {}], iface=REGISTRY_IFACE)
        return "registered"
    except Exception as exc:
        return f"failed: {exc}"


async def grant_app_ids(bus: MessageBus, token: str) -> list[str] | str:
    """App ids the permission store recorded the grant under (table remote-desktop)."""
    try:
        reply = await bus.call(
            Message(
                destination=PERM_BUS,
                path=PERM_PATH,
                interface=PERM_BUS,
                member="Lookup",
                signature="ss",
                body=["remote-desktop", token],
            )
        )
        if reply.message_type == MessageType.ERROR:
            return f"lookup failed: {reply.error_name}"
        return sorted(_unwrap(reply.body[0]).keys())
    except Exception as exc:
        return f"lookup failed: {exc}"


def load_token(path: Path) -> str | None:
    try:
        return json.loads(path.read_text()).get("restore_token")
    except (OSError, ValueError):
        return None


def save_token(path: Path, token: str, app_id: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "restore_token": token,
                "app_id": app_id,
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )
    )
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def ask(question: str) -> str | None:
    if not sys.stdin.isatty():
        return None
    try:
        return input(f"  ? {question} ").strip().lower() or None
    except EOFError:
        return None


# ── libei side ──────────────────────────────────────────────────────────────


def _region_info(libei, region) -> dict:
    # snegg's Region.position reads region_get_x twice; go to the C call.
    return {
        "x": libei.region_get_x(region),
        "y": libei.region_get_y(region),
        "w": libei.region_get_width(region),
        "h": libei.region_get_height(region),
        "physical_scale": libei.region_get_physical_scale(region),
    }


def event_type(ev):
    """``ev.event_type`` or None: snegg's enum lacks newer libei events
    (EI_EVENT_PONG=90, EI_EVENT_SYNC=91) and raises ValueError on them."""
    from snegg.c.libei import libei
    from snegg.ei import EventType

    raw = libei.event_get_type(ev._cobject)
    try:
        return EventType(raw)
    except ValueError:
        return None


def collect_devices(fd: int, settle: float = 1.5, timeout: float = 8.0):
    """Bind every pointer capability and report what the compositor offers."""
    from snegg.c.libei import libei
    from snegg.ei import DeviceCapability, EventType, Sender

    sender = Sender.create_for_fd(
        os.fdopen(fd, "rb", closefd=False), name="perpetua-probe"
    )
    poller = select.poll()
    poller.register(sender.fd, select.POLLIN)
    devices, report = {}, []
    deadline = time.monotonic() + timeout
    quiet_since = None
    caps = (
        DeviceCapability.POINTER,
        DeviceCapability.POINTER_ABSOLUTE,
        DeviceCapability.BUTTON,
        DeviceCapability.SCROLL,
    )
    while time.monotonic() < deadline:
        got = False
        if poller.poll(100):
            sender.dispatch()
        for ev in sender.events:
            got = True
            et = event_type(ev)
            if et == EventType.SEAT_ADDED:
                ev.seat.bind(caps)
            elif et == EventType.DEVICE_ADDED:
                dev = ev.device
                regions, idx = [], 0
                while True:
                    r = libei.device_get_region(dev._cobject, idx)
                    if not r:
                        break
                    regions.append(_region_info(libei, r))
                    idx += 1
                info = {
                    "name": dev.name,
                    "capabilities": [c.name for c in dev.capabilities],
                    "regions": regions,
                }
                devices[dev.name] = dev
                report.append(info)
            elif et == EventType.DEVICE_RESUMED:
                ev.device.start_emulating()
            elif et == EventType.DISCONNECT:
                return sender, devices, report, "disconnected by compositor"
        if got:
            quiet_since = None
        elif devices:
            quiet_since = quiet_since or time.monotonic()
            if time.monotonic() - quiet_since >= settle:
                break
    return sender, devices, report, None


async def shell_pointer(bus: MessageBus):
    reply = await bus.call(
        Message(
            destination="org.gnome.Shell",
            path="/org/gnome/Shell",
            interface="org.gnome.Shell",
            member="Eval",
            signature="s",
            body=["global.get_pointer()"],
        )
    )
    if reply.message_type == MessageType.ERROR or not reply.body[0]:
        return None
    x, y, _mods = json.loads(reply.body[1])
    return float(x), float(y)


async def positioning_test(bus, sender, devices, report, truth: str | None) -> list:
    from snegg.ei import DeviceCapability

    results = []
    abs_dev = next(
        (
            d
            for d in devices.values()
            if DeviceCapability.POINTER_ABSOLUTE in d.capabilities
        ),
        None,
    )
    rel_dev = next(
        (d for d in devices.values() if DeviceCapability.POINTER in d.capabilities),
        None,
    )

    async def observe(label, expected):
        await asyncio.sleep(0.35)
        actual = await shell_pointer(bus) if truth == "shell-eval" else None
        entry = {"step": label, "expected": expected, "actual": actual}
        if actual is None:
            entry["sprite_followed"] = ask(
                f"[{label}] did the cursor SPRITE move there? [y/n]"
            )
        else:
            entry["error_px"] = (
                (round(actual[0] - expected[0], 2), round(actual[1] - expected[1], 2))
                if expected
                else None
            )
        print(f"  {label}: expected={expected} actual={actual}")
        results.append(entry)

    if abs_dev is not None:
        info = next(r for r in report if r["name"] == abs_dev.name)
        for i, reg in enumerate(info["regions"]):
            x, y, w, h = reg["x"], reg["y"], reg["w"], reg["h"]
            points = {
                "centre": (x + w / 2, y + h / 2),
                "top-left": (x + 1, y + 1),
                "bottom-right": (x + w - 2, y + h - 2),
            }
            for name, (px, py) in points.items():
                abs_dev.pointer_motion_absolute(px, py).frame()
                sender.dispatch()
                await observe(f"abs region{i} {name}", (px, py))
        # Outside every region: what does the compositor do with it?
        if info["regions"]:
            reg = info["regions"][0]
            px, py = reg["x"] - 50, reg["y"] - 50
            abs_dev.pointer_motion_absolute(px, py).frame()
            sender.dispatch()
            await observe("abs outside region0", (px, py))
    else:
        print(
            "  no POINTER_ABSOLUTE device: the server-authoritative design is not possible here"
        )

    if rel_dev is not None:
        start = await shell_pointer(bus) if truth == "shell-eval" else None
        for dx, dy in ((100, 0), (0, 100), (-100, 0), (0, -100)):
            rel_dev.pointer_motion(dx, dy).frame()
            sender.dispatch()
            expected = (start[0] + dx, start[1] + dy) if start else None
            await observe(f"rel ({dx},{dy})", expected)
            if start and results[-1]["actual"]:
                start = results[-1]["actual"]
    return results


# ── main ────────────────────────────────────────────────────────────────────


async def run(args) -> dict:
    out: dict = {
        "argv": sys.argv[1:],
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP"),
        "session_type": os.environ.get("XDG_SESSION_TYPE"),
    }
    bus = await MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True).connect()
    portal = Portal(bus)

    if args.app_id:
        out["register"] = await register_app_id(portal, args.app_id)
        print(f"Registry.Register({args.app_id}): {out['register']}")
    out["portal_version"] = await get_version(portal)
    print(f"RemoteDesktop portal version: {out['portal_version']}")
    if (out["portal_version"] or 0) < 2:
        print("  version < 2: no persistence and no ConnectToEIS on this system")

    token_in = None if args.no_token else load_token(args.token_file)
    out["token_in"] = bool(token_in)
    print(f"saved token: {'yes' if token_in else 'no'}  ({args.token_file})")

    code, res, _ = await portal.request(
        "CreateSession",
        "a{sv}",
        [],
        {"session_handle_token": Variant("s", "perpetua_s" + secrets.token_hex(4))},
    )
    if code != 0:
        out["error"] = f"CreateSession: {RESPONSE_TEXT.get(code, code)}"
        return out
    session = res["session_handle"]

    sel = {
        "types": Variant("u", args.devices),
        "persist_mode": Variant("u", PERSIST_UNTIL_REVOKED),
    }
    if token_in:
        sel["restore_token"] = Variant("s", token_in)
    code, _, _ = await portal.request("SelectDevices", "oa{sv}", [session], sel)
    if code != 0:
        out["error"] = f"SelectDevices: {RESPONSE_TEXT.get(code, code)}"
        return out

    print("Start... (answer the dialog if one appears)")
    code, res, latency = await portal.request("Start", "osa{sv}", [session, ""], {})
    out.update(
        start_response=RESPONSE_TEXT.get(code, code),
        start_latency_s=round(latency, 3),
        devices_granted=res.get("devices"),
    )
    print(
        f"Start: {out['start_response']} in {latency:.2f}s, devices={res.get('devices')}"
    )
    if code != 0:
        if token_in and code in (1, 2):
            print(
                "  (a refused restore invalidates the token; delete it with --forget)"
            )
        return out

    out["dialog_seen"] = ask("did a permission dialog appear? [y/n]")
    token_out = res.get("restore_token")
    out["token_out"] = bool(token_out)
    out["token_rotated"] = bool(token_in and token_out and token_out != token_in)
    if token_out:
        save_token(args.token_file, token_out, args.app_id)
        out["grant_app_ids"] = await grant_app_ids(bus, token_out)
        print(
            f"new token saved (rotated={out['token_rotated']}); "
            f"grant stored under app ids: {out['grant_app_ids']}"
        )
    else:
        print("  no restore_token in Start results: persistence NOT supported here")

    reply = await portal.call("ConnectToEIS", "oa{sv}", [session, {}])
    fd_index = reply.body[0]
    fds = reply.unix_fds or []
    fd = (
        fds[fd_index] if isinstance(fd_index, int) and fd_index < len(fds) else fd_index
    )
    print(f"ConnectToEIS: fd={fd}")

    sender, devices, report, err = collect_devices(fd)
    out["ei_devices"] = report
    if err:
        out["ei_error"] = err
    for d in report:
        print(f"  device {d['name']!r}: {d['capabilities']}")
        for r in d["regions"]:
            print(
                f"    region {r['w']}x{r['h']}+{r['x']}+{r['y']} scale={r['physical_scale']}"
            )
    has_abs = any(
        "POINTER_ABSOLUTE" in d["capabilities"] and d["regions"] for d in report
    )
    out["absolute_with_regions"] = has_abs
    print(f"POINTER_ABSOLUTE with regions: {'YES' if has_abs else 'NO'}")

    if args.move:
        print("positioning test" + (" (truth: gnome-shell Eval)" if args.truth else ""))
        out["positioning"] = await positioning_test(
            bus, sender, devices, report, args.truth
        )

    # Free the libei context while EIS is still connected: freed after Close,
    # libei writes its disconnect to a dead socket ("failed to send message").
    del sender, devices
    gc.collect()
    await portal.call(
        "Close", "", [], iface="org.freedesktop.portal.Session", path=session
    )
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--app-id", help="register this app id first (portal >= 1.19.4)")
    p.add_argument(
        "--devices",
        type=int,
        default=DEVICE_POINTER,
        help="device types bitmask (1 keyboard, 2 pointer); default 2",
    )
    p.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    p.add_argument("--no-token", action="store_true", help="ignore the saved token")
    p.add_argument(
        "--forget",
        action="store_true",
        help="delete the saved token first (falsifier: dialog must appear)",
    )
    p.add_argument("--move", action="store_true", help="run the positioning test")
    p.add_argument(
        "--truth",
        choices=["shell-eval"],
        help="read the real pointer via gnome-shell Eval (unsafe mode)",
    )
    p.add_argument("--json", type=Path, help="write the raw result here")
    args = p.parse_args()

    if args.forget:
        args.token_file.unlink(missing_ok=True)
        print(f"removed {args.token_file}; the next Start MUST show the dialog")
    out = asyncio.run(run(args))
    if args.json:
        args.json.write_text(json.dumps(out, indent=2, default=str))
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
