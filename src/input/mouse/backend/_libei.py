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

"""libei mouse backend for Wayland compositors (GNOME >= 45, KDE >= 6.1).

MouseListener uses the InputCapture portal to capture the cursor at
screen-edge barriers.  MouseController uses the RemoteDesktop portal
to emulate pointer input via libei.

Discrete scroll is called through the C bindings because snegg does
not expose scroll methods on Device.
"""

import os
import queue
import time
import threading
import enum
import select as _select

from evdev import ecodes
from snegg.ei import Sender, EventType, DeviceCapability
from snegg.c.libei import libei
from snegg.oeffis import Oeffis, DeviceType, DisconnectedError, SessionClosedError

from input.utils import ButtonMapping
from utils.logging import get_logger

Button = enum.Enum(
    "Button",
    module=__name__,
    names=[("unknown", None), ("left", 1), ("middle", 2), ("right", 3)],
)

ButtonToEcodeMap = {
    Button.left.name: ecodes.BTN_LEFT,
    Button.middle.name: ecodes.BTN_MIDDLE,
    Button.right.name: ecodes.BTN_RIGHT,
}

# Linux input event codes (from linux/input-event-codes.h)
_BTN_LEFT = 0x110  # 272
_BTN_RIGHT = 0x111  # 273
_BTN_MIDDLE = 0x112  # 274

_LINUX_BTN_TO_MAPPING = {
    _BTN_LEFT: ButtonMapping.left,
    _BTN_RIGHT: ButtonMapping.right,
    _BTN_MIDDLE: ButtonMapping.middle,
}


def _now_us() -> int:
    return int(time.monotonic() * 1_000_000)


_CONTROLLER_CAPABILITIES = (
    DeviceCapability.POINTER,
    DeviceCapability.POINTER_ABSOLUTE,
    DeviceCapability.BUTTON,
    DeviceCapability.SCROLL,
)


class _EiConnection:
    """RemoteDesktop portal session with libei Sender and dispatch thread."""

    def __init__(self):
        self._reset()

    def _reset(self):
        self._device = None
        self._sender: Sender | None = None
        self._oeffis: Oeffis | None = None
        self._eis_file = None
        self._paused = threading.Event()
        self._error: Exception | None = None
        self._dispatch_thread: threading.Thread | None = None
        self._has_pointer = False
        self._has_pointer_abs = False

    @property
    def device(self):
        if self._error is not None:
            raise self._error
        return self._device

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def reconnect(self):
        self._reset()
        self.connect()

    def connect(self):
        if self._device is not None:
            return

        poller = _select.poll()

        # Portal session
        self._oeffis = Oeffis.create(devices=DeviceType.POINTER)
        poller.register(self._oeffis.fd, _select.POLLIN)

        # Obtain EIS fd
        eis_fd = None
        for _ in range(50):
            if poller.poll(200):
                try:
                    self._oeffis.dispatch()
                except (DisconnectedError, SessionClosedError) as exc:
                    raise RuntimeError(
                        f"libei: portal rejected connection: {exc}"
                    ) from exc
                try:
                    eis_fd = self._oeffis.eis_fd
                    if eis_fd is not None:
                        break
                except (DisconnectedError, SessionClosedError) as exc:
                    raise RuntimeError(f"libei: portal disconnected: {exc}") from exc
                except AttributeError:
                    continue

        if eis_fd is None:
            raise RuntimeError("libei: failed to obtain EIS fd from portal")

        poller.unregister(self._oeffis.fd)

        # Libei Sender
        self._eis_file = os.fdopen(eis_fd, "rb", closefd=False)
        self._sender = Sender.create_for_fd(
            self._eis_file, name="perpetua-mouse-controller"
        )
        poller.register(self._sender.fd, _select.POLLIN)

        # Seat bind and device acquisition loop
        seat_bound = False
        device = None
        for _ in range(100):
            if poller.poll(100):
                self._sender.dispatch()
            for event in self._sender.events:
                if event.event_type == EventType.SEAT_ADDED and not seat_bound:
                    event.seat.bind(_CONTROLLER_CAPABILITIES)
                    seat_bound = True
                elif event.event_type == EventType.DEVICE_ADDED and device is None:
                    device = event.device
                    device.start_emulating(0)
                elif (
                    event.event_type == EventType.DEVICE_RESUMED and device is not None
                ):
                    caps = device.capabilities
                    self._has_pointer = DeviceCapability.POINTER in caps
                    self._has_pointer_abs = DeviceCapability.POINTER_ABSOLUTE in caps
                    self._device = device
                    self._start_dispatch()
                    return

        raise RuntimeError("libei: no device received after seat bind")

    def _start_dispatch(self):
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, daemon=True
        )
        self._dispatch_thread.start()

    def _dispatch_loop(self):
        poller = _select.poll()
        poller.register(self._sender.fd, _select.POLLIN)

        while self._error is None:
            try:
                ready = poller.poll(500)
            except Exception:
                break

            if not ready:
                continue

            try:
                self._sender.dispatch()
            except Exception as exc:
                self._error = RuntimeError(f"libei: dispatch error: {exc}")
                break

            for event in self._sender.events:
                etype = event.event_type

                if etype == EventType.DEVICE_PAUSED:
                    self._paused.set()

                elif etype == EventType.DEVICE_RESUMED:
                    self._paused.clear()
                    if self._device is not None:
                        self._device.start_emulating(0)

                elif etype == EventType.DEVICE_REMOVED:
                    self._error = RuntimeError("libei: device removed by compositor")
                    self._device = None
                    return

                elif etype == EventType.DISCONNECT:
                    self._error = RuntimeError("libei: disconnected by compositor")
                    self._device = None
                    return


_conn: _EiConnection | None = None
_conn_lock = threading.Lock()


def _get_connection() -> _EiConnection:
    global _conn
    with _conn_lock:
        if _conn is None:
            _conn = _EiConnection()
            _conn.connect()
        return _conn


def _reconnect() -> _EiConnection:
    global _conn
    with _conn_lock:
        _conn = _EiConnection()
        _conn.connect()
        return _conn


def _scroll_discrete(device, dx: int, dy: int):
    libei.device_scroll_discrete(device._cobject, dx, dy)


class MouseController:
    """Emulates pointer input through the RemoteDesktop portal via libei."""

    def __init__(self):
        self._x = 0
        self._y = 0
        self._conn = _get_connection()

    def _ensure_device(self):
        if self._conn._error is not None:
            self._conn = _reconnect()
        return self._conn.device

    @property
    def position(self) -> tuple[int, int]:
        return (self._x, self._y)

    @position.setter
    def position(self, value: tuple[int, int]):
        x, y = int(value[0]), int(value[1])
        if not self._conn.paused:
            device = self._ensure_device()
            if self._conn._has_pointer and not self._conn._has_pointer_abs:
                device.pointer_motion(float(x - self._x), float(y - self._y))
            else:
                device.pointer_motion_absolute(float(x), float(y))
            device.frame(_now_us())
        self._x = x
        self._y = y

    def move(self, dx: int, dy: int):
        dx, dy = int(dx), int(dy)
        self._x += dx
        self._y += dy
        if not self._conn.paused:
            device = self._ensure_device()
            if self._conn._has_pointer:
                device.pointer_motion(float(dx), float(dy))
            else:
                device.pointer_motion_absolute(float(self._x), float(self._y))
            device.frame(_now_us())

    def press(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        if code is not None and not self._conn.paused:
            self._ensure_device().button_button(code, True).frame(_now_us())

    def release(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        if code is not None and not self._conn.paused:
            self._ensure_device().button_button(code, False).frame(_now_us())

    def click(self, button: Button, count: int = 1):
        for _ in range(count):
            self.press(button)
            self.release(button)

    def scroll(self, dx: int, dy: int):
        if not self._conn.paused and (dx or dy):
            # 1 wheel click = 120 hi-res units
            device = self._ensure_device()
            _scroll_discrete(device, int(dx) * 120, int(dy) * 120)
            device.frame(_now_us())


_LISTENER_CAPABILITIES = (
    DeviceCapability.POINTER,
    DeviceCapability.POINTER_ABSOLUTE,
    DeviceCapability.TOUCH,
    DeviceCapability.SCROLL,
    DeviceCapability.BUTTON,
)


class _CaptureSession:
    """State of an active InputCapture portal session."""

    __slots__ = (
        "portal",
        "receiver",
        "barrier_map",
        "poller",
        "captured",
        "pending_activation",
        "scroll_accum_x",
        "scroll_accum_y",
    )

    def __init__(self, portal, receiver, barrier_map, poller):
        self.portal = portal
        self.receiver = receiver
        self.barrier_map: dict[int, str] = barrier_map
        self.poller = poller
        self.captured: bool = False
        self.pending_activation: bool = False
        self.scroll_accum_x: float = 0.0
        self.scroll_accum_y: float = 0.0

    @classmethod
    def create(cls, active_edges, logger) -> "_CaptureSession | None":
        """Create a portal session with barriers only for *active_edges*."""
        from pyinputcapture import InputCapturePortal
        from snegg.ei import Receiver

        try:
            portal = InputCapturePortal()
            zones, eis_fd, bmap_list = portal.setup(active_edges)
            logger.debug(f"Session created: zones={zones} edges={active_edges}")

            eis_file = os.fdopen(eis_fd, "rb", closefd=False)
            receiver = Receiver.create_for_fd(eis_file, name="perpetua-cursor-capture")

            portal.enable()
            _wait_for_seat(receiver, logger)

            poller = _select.poll()
            poller.register(receiver.fd, _select.POLLIN)

            barrier_map = {bid: edge for bid, edge in bmap_list}
            return cls(portal, receiver, barrier_map, poller)

        except Exception as exc:
            logger.error(f"Session setup failed: {exc}")
            return None

    def teardown(self):
        """Release capture and close the portal session."""
        if self.captured:
            try:
                self.portal.release(None, None)
            except Exception:
                pass
            self.captured = False
        try:
            self.portal.close()
        except Exception:
            pass

    def release_cursor(self, x, y):
        """Release capture and return cursor to (x, y)."""
        try:
            self.portal.release(x, y)
        except Exception as exc:
            raise RuntimeError(f"Release error: {exc}") from exc
        self.captured = False

    def poll_activated(self):
        """Pop next Activated event from the queue."""
        return self.portal.poll_activated()

    @staticmethod
    def compute_release_pos(cmd, portal):
        """Compute absolute cursor position for release."""
        x = cmd.get("x", -1)
        y = cmd.get("y", -1)
        if x != -1 and y != -1 and portal.zones:
            w, h, x_off, y_off = portal.zones[0]
            return float(x_off + x * w), float(y_off + y * h)
        return None, None


def _wait_for_seat(receiver, logger):
    """Wait for the EIS seat and bind capabilities."""
    poller = _select.poll()
    poller.register(receiver.fd, _select.POLLIN)

    seat_bound = False
    device_ready = False

    for _ in range(100):  # 10 seconds max
        if poller.poll(100):
            receiver.dispatch()

        for event in receiver.events:
            etype = event.event_type

            if etype == EventType.SEAT_ADDED:
                event.seat.bind(_LISTENER_CAPABILITIES)
                receiver.dispatch()
                logger.debug("EIS seat bound (all capabilities)")
                seat_bound = True

            elif etype == EventType.DEVICE_ADDED:
                logger.debug(f"EIS device: {event.device}")

            elif etype == EventType.DEVICE_RESUMED:
                logger.debug("EIS device resumed (ready)")
                device_ready = True

        if seat_bound and device_ready:
            return

    if not seat_bound:
        logger.warning("EIS seat wait timed out (no SEAT_ADDED)")
    elif not device_ready:
        logger.warning("EIS seat bound but no DEVICE_RESUMED (continuing)")


class MouseListener:
    """InputCapture portal listener (daemon thread).

    Events are placed in `event_queue` as tuples:
    ("motion", dx, dy), ("button", mapping, pressed),
    ("scroll", dx, dy), ("barrier", edge, cx, cy).

    The session is recreated when the active edges change
    (workaround for a GNOME Activated signal bug).
    """

    def __init__(self, **kwargs):
        self._thread: threading.Thread | None = None
        self._is_running = False
        self._ready_event = threading.Event()
        self._cmd_queue: queue.Queue = queue.Queue()
        self.event_queue: queue.Queue = queue.Queue()
        self._logger = get_logger(self.__class__.__name__)

    def start(self):
        """Start the daemon thread."""
        if self._is_running:
            return
        self._is_running = True
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        self._logger.debug("InputCapture listener started")

    def stop(self):
        """Stop the daemon thread."""
        if not self._is_running:
            return
        self._is_running = False
        try:
            self._cmd_queue.put({"type": "quit"})
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._logger.debug("InputCapture listener stopped")

    def is_alive(self):
        return self._is_running and self._thread is not None and self._thread.is_alive()

    def update_clients(self, clients):
        """Update active client edges.  Recreates session if edges changed."""
        self._cmd_queue.put({"type": "update_clients", "clients": clients})

    def disable_capture(self, x=-1, y=-1):
        """Release capture (cursor returns to server)."""
        self._cmd_queue.put({"type": "disable_capture", "x": x, "y": y})

    def _thread_main(self):
        """Session lifecycle loop: idle (no session) or active (poll EIS)."""
        logger = self._logger
        active_edges: list[str] = []
        session: _CaptureSession | None = None
        first_session = True

        try:
            while self._is_running:
                if session is None:
                    result = self._idle_wait(active_edges, logger)
                    if result is None:
                        break  # quit
                    active_edges, session = result
                    if session is not None and first_session:
                        self._ready_event.set()
                        first_session = False
                    continue

                action, active_edges = self._active_tick(
                    session,
                    active_edges,
                    logger,
                )

                if action == "quit":
                    session.teardown()
                    break
                elif action == "rebuild":
                    session.teardown()
                    session = (
                        _CaptureSession.create(active_edges, logger)
                        if active_edges
                        else None
                    )
                elif action == "disconnected":
                    session = None

        except Exception as exc:
            logger.error(f"InputCapture thread fatal: {exc}")
            self._ready_event.set()
        finally:
            if session is not None:
                session.teardown()
            logger.debug("Thread exiting")

    # idle phase (no session)
    def _idle_wait(self, active_edges, logger):
        """Wait for commands while no session exists."""
        try:
            cmd = self._cmd_queue.get(timeout=0.1)
        except queue.Empty:
            return active_edges, None

        cmd_type = cmd.get("type")

        if cmd_type == "update_clients":
            new_edges = sorted(k for k, v in cmd.get("clients", {}).items() if v)
            if new_edges:
                active_edges = new_edges
                session = _CaptureSession.create(active_edges, logger)
                return active_edges, session
            return active_edges, None

        if cmd_type == "quit":
            return None

        return active_edges, None

    # active phase (session exists)
    def _active_tick(self, session, active_edges, logger):
        """Single iteration of the active session loop."""
        self._dispatch_pending_activation(session)

        # Drain commands
        action, active_edges = self._process_commands(
            session,
            active_edges,
            logger,
        )
        if action != "continue":
            return action, active_edges

        # Poll EIS events (10ms timeout)
        events = session.poller.poll(10)
        if not events:
            return "continue", active_edges

        try:
            session.receiver.dispatch()
        except Exception as exc:
            logger.error(f"Receiver dispatch error: {exc}")
            return "continue", active_edges

        for event in session.receiver.events:
            result = self._handle_ei_event(event, session, logger)
            if result == "disconnected":
                return "disconnected", active_edges

        return "continue", active_edges

    def _dispatch_pending_activation(self, session: _CaptureSession):
        """Resolve a pending Activated signal from the D-Bus queue."""
        if not session.pending_activation:
            return

        activation = session.poll_activated()
        if activation:
            bid, cx, cy = activation
            edge = session.barrier_map.get(bid)
            if edge:
                self.event_queue.put(("barrier", edge, cx, cy))
            session.pending_activation = False

    def _process_commands(self, session, active_edges, logger):
        """Drain the command queue (non-blocking)."""
        while True:
            try:
                cmd = self._cmd_queue.get_nowait()
            except queue.Empty:
                return "continue", active_edges

            cmd_type = cmd.get("type")

            if cmd_type == "update_clients":
                new_edges = sorted(k for k, v in cmd.get("clients", {}).items() if v)
                if new_edges != sorted(active_edges):
                    return "rebuild", list(new_edges)

            elif cmd_type == "disable_capture":
                if session.captured:
                    cx, cy = _CaptureSession.compute_release_pos(
                        cmd,
                        session.portal,
                    )
                    try:
                        session.release_cursor(cx, cy)
                    except RuntimeError as exc:
                        logger.error(str(exc))

            elif cmd_type == "quit":
                self._is_running = False
                return "quit", active_edges

        return "continue", active_edges

    def _handle_ei_event(self, event, session: _CaptureSession, logger):
        """Process a single EIS event."""
        try:
            etype = event.event_type
        except Exception:
            return None

        try:
            if etype == EventType.POINTER_MOTION:
                if session.captured:
                    pe = event.pointer_event
                    raw_dx, raw_dy = pe.dx, pe.dy
                    if raw_dx != raw_dx or raw_dy != raw_dy:  # NaN check
                        return None
                    dx = int(round(raw_dx))
                    dy = int(round(raw_dy))
                    if dx or dy:
                        self.event_queue.put(("motion", dx, dy))

            elif etype == EventType.DEVICE_START_EMULATING:
                self._handle_start_emulating(session, logger)

            elif etype == EventType.DEVICE_STOP_EMULATING:
                session.captured = False
                session.pending_activation = False

            elif etype == EventType.BUTTON_BUTTON:
                if session.captured:
                    be = event.button_event
                    btn = _LINUX_BTN_TO_MAPPING.get(be.button)
                    if btn is not None:
                        self.event_queue.put(("button", btn, be.is_press))

            elif etype == EventType.SCROLL_DISCRETE:
                if session.captured:
                    # snegg's scroll_event accessor calls ei_event_scroll_get_dx()
                    # which is invalid for SCROLL_DISCRETE (type 603).
                    # Go through the C bindings directly.
                    raw = event._cobject
                    raw_dx = libei.event_scroll_get_discrete_dx(raw)
                    raw_dy = libei.event_scroll_get_discrete_dy(raw)
                    # 120 hi-res units = 1 wheel notch
                    dx = int(raw_dx) // 120
                    dy = int(raw_dy) // 120
                    if dx or dy:
                        self.event_queue.put(("scroll", dx, dy))

            elif etype == EventType.SCROLL_DELTA:
                if session.captured:
                    se = event.scroll_event
                    self._accumulate_scroll(session, se.dx, se.dy)
            elif etype == EventType.SEAT_ADDED:
                event.seat.bind(_LISTENER_CAPABILITIES)
                session.receiver.dispatch()

            elif etype == EventType.DEVICE_PAUSED:
                session.captured = False
                session.pending_activation = False

            elif etype == EventType.DISCONNECT:
                logger.warning("EIS disconnected")
                try:
                    session.portal.close()
                except Exception:
                    pass
                return "disconnected"

        except Exception as exc:
            logger.error(f"EI event error: {exc}")

        return None

    # Pixels-per-click threshold for SCROLL_DELTA (touchpad / smooth scroll).
    _SCROLL_PX_PER_CLICK = 15.0

    def _accumulate_scroll(self, session: _CaptureSession, dx: float, dy: float):
        """Convert pixel-based SCROLL_DELTA into discrete click counts."""
        session.scroll_accum_x += dx
        session.scroll_accum_y += dy

        threshold = self._SCROLL_PX_PER_CLICK
        clicks_x = int(session.scroll_accum_x / threshold)
        clicks_y = int(session.scroll_accum_y / threshold)

        if clicks_x:
            session.scroll_accum_x -= clicks_x * threshold
        if clicks_y:
            session.scroll_accum_y -= clicks_y * threshold
        if clicks_x or clicks_y:
            self.event_queue.put(("scroll", clicks_x, clicks_y))

    def _handle_start_emulating(self, session: _CaptureSession, logger):
        """Resolve barrier activation on DEVICE_START_EMULATING."""
        session.captured = True

        activation = session.poll_activated()
        if activation:
            bid, cx, cy = activation
            edge = session.barrier_map.get(bid)
            logger.debug(f"START_EMULATING: bid={bid} edge={edge} pos=({cx}, {cy})")
            if edge:
                self.event_queue.put(("barrier", edge, cx, cy))
        else:
            # D-Bus Activated not yet received, will be picked up next tick.
            logger.debug("START_EMULATING: awaiting Activated")
            session.pending_activation = True


__all__ = ["MouseListener", "MouseController", "Button"]
