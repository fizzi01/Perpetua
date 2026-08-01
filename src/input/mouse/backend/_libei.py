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
from utils import ExponentialBackoff
from utils.logging import get_logger


def _ei_from_fd(cls, fd: int, name: str):
    """Create a snegg Sender/Receiver, handling both API variants."""
    try:
        f = os.fdopen(fd, "rb", closefd=False)
        return cls.create_for_fd(f, name=name)
    except TypeError:
        return cls.create_for_fd(fd, name=name)


def _suppress_libei_stderr():
    """Redirect stderr fd to /dev/null to silence libei C-level debug spam."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        saved = os.dup(2)
        os.dup2(devnull, 2)
        os.close(devnull)
        return saved
    except OSError:
        return None


def _restore_stderr(saved_fd):
    """Restore stderr from a saved fd."""
    if saved_fd is not None:
        try:
            os.dup2(saved_fd, 2)
            os.close(saved_fd)
        except OSError:
            pass


# Portal failures that mean "the user has not granted access", where retrying
# changes nothing until they do.
_PERMISSION_HINTS = (
    "not authorized",
    "not authorised",
    "denied",
    "cancelled",
    "canceled",
    "no such interface",
    # ashpd's own wording for a dialog the user dismissed, typo included.
    "didn't succed",
    "didn't succeed",
)


def _is_permission_failure(reason: str) -> bool:
    lowered = reason.lower()
    return any(hint in lowered for hint in _PERMISSION_HINTS)


#: The extension's wording for "the deadline passed with the dialog unanswered"
#: (``wait_for_setup`` in pyinputcapture). Deliberately this exact phrase and not
#: "timed out": a ``zones``/``set_pointer_barriers`` timeout happens *after* the
#: dialog was answered and is a genuinely transient fault, so widening this to
#: any timeout would put ordinary failures on the human-cadence schedule.
_SETUP_TIMEOUT_HINT = "portal setup timed out"


def _is_setup_timeout(reason: str) -> bool:
    return _SETUP_TIMEOUT_HINT in reason.lower()


def _portal_last_error(portal) -> str | None:
    if portal is None:
        return None
    try:
        detail = portal.last_error
    except Exception:
        return None
    detail = str(detail).strip() if detail else ""
    return detail or None


#: Every session arms the whole edge of every zone, on all four sides.
#:
#: Not "the edges a client is behind": barriers are armed by ``portal.setup()``
#: and never touched again, because the only way to change them on a live
#: session is ``SetPointerBarriers``, which GNOME 46 refuses on an enabled
#: session and cannot recover from (it deletes the working barrier set and
#: installs nothing). Arming all four once removes every portal call after
#: setup, and with it the whole class of failure.
#:
#: A coarse barrier is safe; a missing one is not. An edge with nothing behind
#: it costs one capture/release round trip that Python filters immediately
#: (``_dispatch_pending_activation``, then ``_resolve_cross_screen_target``
#: for a partly bound edge); an edge left unarmed cannot be crossed at all.
_ALL_EDGES = ("bottom", "left", "right", "top")


#: How long to wait for an answer to the permission dialog before giving up on
#: *this* attempt. The extension's own default (120 s) keeps the capture thread
#: blocked long past the point where it is obvious nobody is going to answer,
#: and ``stop()`` can do nothing but watch its join time out.
_SETUP_TIMEOUT = 45.0


#: Marker attribute of pyinputcapture >= 0.3.0, the first build whose tokio
#: runtime is process-global.
#:
#: Anything older shuts that runtime down when a portal object is dropped, which
#: kills the zbus tasks of ashpd's *process-global* D-Bus connection: from then
#: on every portal request in the process is accepted and never answered, so a
#: cancelled dialog never comes back. There is no working around that from here,
#: only reporting it - and a stale ``.so`` shadowing a rebuilt one has happened
#: on this box before.
_REQUIRED_EXTENSION_ATTR = "last_error"
_REQUIRED_EXTENSION_VERSION = "0.3.0"


def _capture_backend_info() -> dict:
    """Which pyinputcapture is actually loaded, and what it can do.

    Logged once at listener start so "I have the new build" is checkable from
    the log instead of being inferred from a warning - a stale ``.so`` shadowing
    the rebuilt one (a build for another Python version leaves both) is
    otherwise invisible.
    """
    info: dict = {
        "module": None,
        "version": None,
        # Tri-state on purpose: True/False once the class was importable,
        # "unknown" when it was not - a missing extension is not a stale one.
        "shared_runtime": "unknown",
    }
    try:
        import pyinputcapture
        from pyinputcapture import InputCapturePortal

        info["module"] = getattr(pyinputcapture, "__file__", None)
        # The Rust ``#[pymodule]`` defines no ``__version__``, so the module
        # attribute is always None and the startup line could never confirm a
        # rebuild. The installed distribution's metadata can.
        version = getattr(pyinputcapture, "__version__", None)
        if not version:
            try:
                from importlib.metadata import version as _distribution_version

                version = _distribution_version("pyinputcapture")
            except Exception:
                version = None
        info["version"] = version
        # The capability, not the version string: metadata can name 0.3.0 while
        # the loaded ``.so`` is an older build sitting earlier on the path.
        info["shared_runtime"] = hasattr(InputCapturePortal, _REQUIRED_EXTENSION_ATTR)
    except Exception as exc:
        info["error"] = str(exc)
    return info


class _PortalNeedsUser(RuntimeError):
    """A failure only the user can resolve, by answering the portal dialog.

    Retrying in a second cannot help, so these are raised instead of a plain
    failure: the caller puts them on a human-paced schedule and stands down
    rather than spinning on a dialog.
    """


class _PortalNotAuthorised(_PortalNeedsUser):
    """Capture was refused: the user denied or dismissed the dialog.

    The answer stays "no" until they act, so nothing but an explicit request
    for capture is worth another attempt.
    """


class _PortalDialogUnanswered(_PortalNeedsUser):
    """``setup()`` gave up waiting for an answer to the permission dialog.

    Not a transient fault, and *not* the same as a refusal: the request may well
    still be live on the portal side with the dialog on screen.
    """


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

    # The dispatch loop polls in 500 ms slices, so this is one slice plus
    # slack - long enough to exit cleanly, short enough not to stall a stop.
    _DISPATCH_JOIN_TIMEOUT = 1.0

    def __init__(self):
        self._reset()

    def _reset(self):
        self._device = None
        self._sender: Sender | None = None
        self._oeffis: Oeffis | None = None
        self._paused = threading.Event()
        self._error: Exception | None = None
        self._dispatch_thread: threading.Thread | None = None
        self._has_pointer = False
        self._has_pointer_abs = False
        self._closing = False

    @property
    def device(self):
        # Read under the module lock so a concurrent _dispatch_loop tear-down
        # (which nulls _device and sets _error) is observed atomically: we
        # either see a live device or re-raise the real reason, never a bare
        # None that would surface as an opaque 'NoneType' attribute error.
        with _conn_lock:
            if self._error is not None:
                raise self._error
            return self._device

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def reconnect(self):
        self.close()
        self._reset()
        self.connect()

    def close(self):
        """Tear the session down and stop the dispatch thread.

        Without this every reconnect leaked a portal session plus a live
        dispatch thread, and the module singleton outlived ``Server.stop()``
        entirely - so a restarted service was handed back a connection whose
        compositor session was already gone.

        snegg's ``Sender``/``Receiver`` have no ``close()``: the libei context
        is refcounted and destroyed when the last Python reference drops, and
        dropping the ``Oeffis`` context is itself what disconnects the portal
        session. So the teardown here is mostly about *ordering* - stop the
        dispatch thread, close the device, then drop the sender before the
        oeffis (whose destruction invalidates the EIS fd the sender uses).
        """
        self._closing = True

        thread = self._dispatch_thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=self._DISPATCH_JOIN_TIMEOUT)
        self._dispatch_thread = None

        device, self._device = self._device, None
        if device is not None:
            try:
                device.stop_emulating()
            except Exception:
                pass
            try:
                device.close()
            except Exception:
                pass

        self._sender = None
        self._oeffis = None

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
        self._sender = _ei_from_fd(Sender, eis_fd, "perpetua-mouse-controller")
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
        saved_stderr = _suppress_libei_stderr()
        poller = _select.poll()
        poller.register(self._sender.fd, _select.POLLIN)

        try:
            while self._error is None and not self._closing:
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
                        # Null the device and set the error atomically so the
                        # controller's `device` property never observes a live
                        # None (which would raise an opaque 'NoneType' error
                        # instead of this reason).
                        with _conn_lock:
                            self._device = None
                            self._error = RuntimeError(
                                "libei: device removed by compositor"
                            )
                        return

                    elif etype == EventType.DISCONNECT:
                        with _conn_lock:
                            self._device = None
                            self._error = RuntimeError(
                                "libei: disconnected by compositor"
                            )
                        return
        finally:
            _restore_stderr(saved_stderr)


_conn: _EiConnection | None = None
_conn_lock = threading.Lock()


def _get_connection() -> _EiConnection:
    global _conn
    with _conn_lock:
        if _conn is None:
            # Build locally and publish the singleton ONLY after connect()
            # succeeds. A connect() that raises (e.g. the RemoteDesktop portal
            # isn't authorized yet) must not leave a half-initialized instance
            # (_device=None, _error=None) cached: that would make _ensure_conn
            # believe the connection is healthy and hand out a None device
            # forever, until the process restarts.
            conn = _EiConnection()
            conn.connect()
            _conn = conn
        return _conn


def _reconnect() -> _EiConnection:
    global _conn
    with _conn_lock:
        # Close the outgoing one first: a bare replacement leaked its portal
        # session and its dispatch thread on every reconnect.
        stale, _conn = _conn, None
    if stale is not None:
        try:
            stale.close()
        except Exception:
            pass
    with _conn_lock:
        conn = _EiConnection()
        conn.connect()
        _conn = conn
        return conn


def shutdown_connection() -> None:
    """Close the module-wide RemoteDesktop connection, if any.

    The singleton used to survive ``Server.stop()``, so the next start was
    handed a connection whose compositor session had already been torn down.
    """
    global _conn
    with _conn_lock:
        conn, _conn = _conn, None
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


class _PortalBackoff(RuntimeError):
    """The portal is known-unavailable and we are waiting out the retry delay.

    Distinct from a real connection error so callers can drop the event
    quietly instead of logging a failure per mouse move.
    """


def _scroll_discrete(device, dx: int, dy: int):
    libei.device_scroll_discrete(device._cobject, dx, dy)


class MouseController:
    """Emulates pointer input through the RemoteDesktop portal via libei."""

    # A failed connect() can spend up to ~20 s polling the portal. Without a
    # floor between attempts that cost was paid on *every* mouse event, which
    # freezes the injection path far worse than the missing portal itself.
    _RECONNECT_MIN_INTERVAL = 1.0
    _RECONNECT_MAX_INTERVAL = 30.0

    def __init__(self):
        self._x = 0
        self._y = 0
        self._logger = get_logger(self.__class__.__name__)
        self._reconnect_backoff = ExponentialBackoff(
            initial_delay=self._RECONNECT_MIN_INTERVAL,
            max_delay=self._RECONNECT_MAX_INTERVAL,
        )
        self._reconnect_at = 0.0
        # Best-effort: if the RemoteDesktop portal isn't ready yet (common right
        # after enabling sharing), don't abort client startup. _ensure_conn
        # establishes the connection lazily on the first injection and keeps
        # retrying until the portal becomes available.
        try:
            self._conn = _get_connection()
        except Exception as exc:
            self._conn = None
            self._logger.warning("libei connection deferred", error=str(exc))

    def _ensure_conn(self) -> "_EiConnection":
        """Return a live connection, (re)connecting lazily if needed.

        Reconnects when there is no connection yet (deferred at startup), when
        the previous one errored, or when the compositor tore the device down.
        Returns a non-None connection, or raises: either the real underlying
        error, or ``_PortalBackoff`` while waiting out the retry interval.
        """
        if (
            self._conn is not None
            and self._conn._error is None
            and self._conn._device is not None
        ):
            return self._conn

        now = time.monotonic()
        if now < self._reconnect_at:
            raise _PortalBackoff(
                f"libei portal unavailable, retrying in {self._reconnect_at - now:.1f}s"
            )
        try:
            self._conn = _reconnect()
        except Exception:
            delay = self._reconnect_backoff.get_next_delay()
            self._reconnect_at = time.monotonic() + delay
            raise
        self._reconnect_backoff.reset()
        self._reconnect_at = 0.0
        return self._conn

    @property
    def position(self) -> tuple[int, int]:
        return (self._x, self._y)

    @position.setter
    def position(self, value: tuple[int, int]):
        x, y = int(value[0]), int(value[1])
        # Ensure the connection first (lazy connect / self-heal); using the
        # returned local keeps the paused/capability reads on a non-None value.
        conn = self._ensure_conn()
        if not conn.paused:
            device = conn.device
            if conn._has_pointer and not conn._has_pointer_abs:
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
        conn = self._ensure_conn()
        if not conn.paused:
            device = conn.device
            if conn._has_pointer:
                device.pointer_motion(float(dx), float(dy))
            else:
                device.pointer_motion_absolute(float(self._x), float(self._y))
            device.frame(_now_us())

    def press(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        if code is None:
            return
        conn = self._ensure_conn()
        if not conn.paused:
            conn.device.button_button(code, True).frame(_now_us())

    def release(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        if code is None:
            return
        conn = self._ensure_conn()
        if not conn.paused:
            conn.device.button_button(code, False).frame(_now_us())

    def click(self, button: Button, count: int = 1):
        for _ in range(count):
            self.press(button)
            self.release(button)

    def scroll(self, dx: int, dy: int):
        if not (dx or dy):
            return
        conn = self._ensure_conn()
        if not conn.paused:
            # 1 wheel click = 120 hi-res units
            device = conn.device
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

    # Inset applied to a release point so the cursor doesn't land exactly on
    # a barrier line. Only one pixel: ``ignore_next_activation`` already
    # absorbs the spurious recapture, and a bigger inset is directly visible
    # as the cursor refusing to sit on the border it was pushed from.
    _RELEASE_EDGE_MARGIN = 1.0

    __slots__ = (
        "portal",
        "receiver",
        "barrier_map",
        "poller",
        "captured",
        "pending_activation",
        "scroll_accum_x",
        "scroll_accum_y",
        "last_activation_id",
        "ignore_next_activation",
        "armed_edges",
        "dead",
    )

    def __init__(self, portal, receiver, barrier_map, poller, armed_edges):
        self.portal = portal
        self.receiver = receiver
        self.barrier_map: dict[int, str] = barrier_map
        self.poller = poller
        self.captured: bool = False
        self.pending_activation: bool = False
        self.scroll_accum_x: float = 0.0
        self.scroll_accum_y: float = 0.0
        self.last_activation_id: int = 0
        self.ignore_next_activation: bool = False
        # Edges ``setup()`` armed - always all four (see ``_ALL_EDGES``).
        # Diagnostic only: nothing re-arms, so this never changes.
        self.armed_edges: set[str] = set(armed_edges)
        # Set once the compositor has torn the session down: no further
        # portal call can succeed, and calling one anyway is the most likely
        # way to wedge on a blocking D-Bus round trip.
        self.dead: bool = False

    @classmethod
    def create(cls, logger, keep_waiting=None) -> "_CaptureSession | None":
        """Create a portal session with whole-edge barriers on all four sides.

        This is the *only* place barriers are ever armed. There is deliberately
        no way to change them afterwards: see ``_ALL_EDGES``.

        A fresh portal object per attempt, always: the object is cheap and the
        D-Bus connection carrying the request is process-global in the extension,
        so it outlives any one of them. Reusing the object of a timed-out attempt
        (an earlier design) could only wedge on that attempt's own task.
        """
        from pyinputcapture import InputCapturePortal
        from snegg.ei import Receiver

        portal = None
        try:
            portal = InputCapturePortal()
            zones, eis_fd, bmap_list = cls._setup(portal)
            logger.debug(f"Session created: zones={zones} edges={list(_ALL_EDGES)}")

            receiver = _ei_from_fd(Receiver, eis_fd, "perpetua-cursor-capture")

            portal.enable()
            _wait_for_seat(receiver, logger, keep_waiting)

            poller = _select.poll()
            poller.register(receiver.fd, _select.POLLIN)

            barrier_map = {bid: edge for bid, edge in bmap_list}
            session = cls(portal, receiver, barrier_map, poller, _ALL_EDGES)
            logger.debug(f"Barriers armed by setup(): barrier_map={barrier_map}")
            return session

        except Exception as exc:
            reason = str(exc)
            detail = _portal_last_error(portal)
            if detail and detail not in reason:
                # The extension's own explanation of the failure, through the
                # structured logger. The capture thread has fd 2 pointed at
                # /dev/null (libei's dispatch spam is real), so what the portal
                # task printed there used to be unrecoverable.
                reason = f"{reason} ({detail})"
            if portal is not None:
                try:
                    portal.close()
                except Exception:
                    pass
            if _is_permission_failure(reason):
                # Not a transient fault: nothing changes until the user grants
                # access, so say so and let the caller stop retrying instead of
                # burning attempts as if the next one could differ.
                raise _PortalNotAuthorised(reason) from exc
            if _is_setup_timeout(reason):
                # The dialog may still be on screen, unanswered. Not transient,
                # so it goes on the human-paced schedule rather than the backoff.
                raise _PortalDialogUnanswered(reason) from exc
            logger.error(f"Session setup failed: {reason}")
            return None

    @staticmethod
    def _setup(portal):
        """``portal.setup`` with a bounded wait for the permission dialog."""
        return portal.setup(list(_ALL_EDGES), timeout=_SETUP_TIMEOUT)

    def teardown(self):
        """Release capture and close the portal session.

        Skips every portal call once the session is ``dead``: the compositor
        already dropped it, so ``release``/``close`` would only block on a
        D-Bus reply that never comes - with the GIL held, which freezes the
        whole daemon rather than just this thread.
        """
        if self.dead:
            return
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
        """Check for a new activation via atomics (no queue).

        Returns (barrier_id, cx, cy) if a new activation arrived,
        otherwise None.
        """
        aid = self.portal.activation_id
        if aid == self.last_activation_id:
            return None
        self.last_activation_id = aid
        bid = self.portal.barrier_id
        cx, cy = self.portal.cursor_position
        return (bid, cx, cy)

    @staticmethod
    def compute_release_pos(cmd, portal):
        """Compute absolute cursor position for release.

        The incoming ``(x, y)`` is normalised over the server's *virtual
        desktop*, so it must be denormalised over the union of every portal
        zone - not over ``zones[0]``, which on a multi-monitor server is just
        one output and lands the cursor on the wrong screen.

        Clamps the result at least ``_RELEASE_EDGE_MARGIN`` px inside the
        union so the cursor doesn't land exactly on a barrier line and
        trigger an immediate recapture.
        """
        x = cmd.get("x", -1)
        y = cmd.get("y", -1)
        if x == -1 or y == -1 or not portal.zones:
            return None, None

        min_x = min(z[2] for z in portal.zones)
        min_y = min(z[3] for z in portal.zones)
        max_x = max(z[2] + z[0] for z in portal.zones)
        max_y = max(z[3] + z[1] for z in portal.zones)
        width = max(1, max_x - min_x)
        height = max(1, max_y - min_y)

        margin = _CaptureSession._RELEASE_EDGE_MARGIN
        abs_x = max(min_x + margin, min(max_x - margin, float(min_x + x * width)))
        abs_y = max(min_y + margin, min(max_y - margin, float(min_y + y * height)))
        return abs_x, abs_y


def _wait_for_seat(receiver, logger, keep_waiting=None):
    """Wait for the EIS seat and bind capabilities.

    ``keep_waiting`` is polled before starting and on every iteration, so a
    ``stop()`` during this 10-second wait aborts it: otherwise the capture
    thread ignores the quit request for long enough to blow through the join
    timeout and outlive the service that owns it.
    """
    if keep_waiting is not None and not keep_waiting():
        logger.debug("EIS seat wait skipped (stopping)")
        return

    poller = _select.poll()
    poller.register(receiver.fd, _select.POLLIN)

    seat_bound = False
    device_ready = False

    for _ in range(100):  # 10 seconds max
        if keep_waiting is not None and not keep_waiting():
            logger.debug("EIS seat wait aborted (stopping)")
            return
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


_NO_SESSION = object()  # sentinel: no session yet (distinct from None = quit)


#: Held for the duration of one ``_CaptureSession.create``, process-wide.
#:
#: Two dialogs must never be on screen at once, and the schedule alone cannot
#: promise that: a *new* listener object built after ``Server.cleanup()`` cleared
#: ``_components`` knows nothing about the old thread still sitting inside
#: ``setup()``. Module-level for exactly that case. It is not a time floor -
#: measurement says a retry at delay 0 is answered normally - so an attempt that
#: cannot take the lock is simply dropped and rescheduled.
_CREATE_LOCK = threading.Lock()


class MouseListener:
    """InputCapture portal listener (daemon thread).

    Events are delivered via callbacks (on_move, on_click, on_scroll,
    on_barrier) called from the daemon thread.

    The session is created once and kept: re-running ``portal.setup()`` hangs
    the GNOME portal. It arms whole-edge barriers on all four sides and never
    touches them again (see ``_ALL_EDGES``); which edges actually lead to a
    client is a Python-side filter, kept in ``_active_edges``.

    ``on_state(healthy, reason)`` reports capture health so the service layer
    can see a session that died and never came back, instead of reading the
    thread as alive and assuming all is well.
    """

    # Initial delay of the reconnect backoff. There is no inner retry count any
    # more: session creation is attempted once per scheduling cycle, and the
    # backoff owns the cadence (see ``_create_session_once``).
    _SESSION_RETRY_DELAY = 1.0  # seconds
    _SESSION_RETRY_MAX_DELAY = 30.0  # backoff ceiling for reconnects
    # Granularity of every interruptible sleep in the thread, so a stop()
    # issued mid-backoff is observed well inside the join timeout.
    _SLEEP_SLICE = 0.05
    # Consecutive dialog-class failures (refused, or left unanswered) after
    # which the portal stops being asked. Each attempt puts a permission dialog
    # in front of the user, and re-opening it forever is not a retry policy, it
    # is a nuisance. Capture stays off until the user asks for it again (see
    # ``_idle_wait`` and ``request_capture``).
    _MAX_NEEDS_USER_ATTEMPTS = 3
    # Cadence for that class. Deliberately not the backoff's opening delay: a
    # request the user has not answered yet is still live on the portal side,
    # and a second CreateSession a second later is how GNOME ends up answering
    # neither of them.
    _DIALOG_RETRY_DELAY = 30.0
    # How long a deferred start waits for the previous capture thread to leave a
    # wedged ``portal.setup()`` before giving up on it. Its own wait is bounded
    # by ``_SETUP_TIMEOUT``, plus room for the teardown that follows.
    _PREVIOUS_THREAD_WAIT = _SETUP_TIMEOUT + 15.0

    def __init__(
        self,
        on_move=None,
        on_click=None,
        on_scroll=None,
        on_barrier=None,
        on_state=None,
        **kwargs,
    ):
        self._on_move = on_move
        self._on_click = on_click
        self._on_scroll = on_scroll
        self._on_barrier = on_barrier
        self._on_state = on_state
        self._thread: threading.Thread | None = None
        self._is_running = False
        self._clients_active = False
        # Edges that currently lead to a client. Barriers are armed on all
        # four regardless (see ``_ALL_EDGES``); this is the Python-side filter
        # that decides which activations are worth acting on.
        self._active_edges: set[str] = set()
        self._ready_event = threading.Event()
        self._cmd_queue: queue.Queue = queue.Queue()
        # Published for ``current_activation_id`` right before on_barrier fires.
        self._current_activation_id = 0
        # Reason string while capture is waiting on the user (refused, or a
        # dialog nobody answered), else None.
        self._unauthorised: str | None = None
        # Consecutive dialog-class failures, and the latch that stops asking
        # once they hit ``_MAX_NEEDS_USER_ATTEMPTS``.
        self._needs_user_attempts = 0
        self._capture_blocked = False
        # Attempt counter and the time of the last one, for the per-attempt
        # diagnostic line. The daemon log's "reconnecting in 30.0s" was
        # contradicted by its own timestamps for a whole round of debugging, and
        # nothing said how many dialogs had actually been asked for.
        self._attempts = 0
        self._last_attempt_at = 0.0
        # Set while a start is waiting for the previous capture thread to exit,
        # so only one waiter can ever exist.
        self._deferred_start = False
        # Whether capture is wanted at all, so a stop can cancel a deferred
        # start instead of being undone by it.
        self._start_wanted = False
        # Session health, read by ``is_alive`` so the service layer's
        # restart-on-dead check can actually fire.
        self._has_session = False
        self._reconnect_pending = False
        self._backoff = ExponentialBackoff(
            initial_delay=self._SESSION_RETRY_DELAY,
            max_delay=self._SESSION_RETRY_MAX_DELAY,
        )
        # Monotonic deadline for the next reconnect attempt, or 0.0 for "now".
        self._reconnect_at = 0.0
        self._logger = get_logger(self.__class__.__name__)

    def start(self):
        """Start the daemon thread.

        Waits out the *previous* capture thread rather than running two. ``stop()``
        joins with a timeout, and that join times out precisely when the thread
        is still inside ``portal.setup()`` on an unanswered permission dialog -
        at which point ``_is_running`` is already False and ``is_alive()`` reports
        dead, so the service layer restarts us. Two capture threads polling one
        EIS receiver is nothing anyone wants; that two of them cannot raise two
        dialogs is ``_CREATE_LOCK``'s job, not this one's.

        Refusing is not the end of it: the start is *deferred* onto a waiter
        that starts the thread as soon as the old one exits. Only logging it -
        as this used to - left capture dead for the rest of the process's life,
        because nothing ever asked again.

        Returns whether capture is now running or scheduled to.
        """
        if self._is_running:
            return True
        self._start_wanted = True
        previous = self._thread
        if previous is not None and previous.is_alive():
            self._defer_start(previous)
            return True
        self._is_running = True
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        self._logger.debug("InputCapture listener started")
        return True

    def _defer_start(self, previous: threading.Thread):
        """Start once ``previous`` exits, on a one-shot waiter thread."""
        if self._deferred_start:
            return
        self._deferred_start = True
        self._logger.warning(
            "InputCapture listener start deferred: the previous capture thread "
            "is still alive (most likely blocked in portal.setup() on an "
            "unanswered permission dialog); starting as soon as it exits "
            "rather than running two capture threads"
        )

        def _wait_and_start():
            try:
                previous.join(timeout=self._PREVIOUS_THREAD_WAIT)
                if previous.is_alive():
                    self._logger.error(
                        "InputCapture listener not started: the previous capture "
                        "thread is still blocked after "
                        f"{self._PREVIOUS_THREAD_WAIT:.0f}s"
                    )
                    return
            finally:
                self._deferred_start = False
            if not self._start_wanted:
                # Stopped while we waited. Starting now would resurrect capture
                # after an explicit stop - and raise a dialog for it.
                self._logger.debug("Deferred start dropped: a stop came first")
                return
            self.start()

        threading.Thread(target=_wait_and_start, daemon=True).start()

    def stop(self):
        """Stop the daemon thread."""
        # Before the early return: a stop must also cancel a start that is still
        # waiting for a wedged thread to exit, even though nothing runs yet.
        self._start_wanted = False
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
        """Whether capture is working, or at least still trying to.

        Deliberately more than "the thread exists": a thread idling with a
        dead session and no reconnect pending is not capturing anything, and
        reporting it alive is what stopped the service layer from ever
        restarting a listener whose portal session had gone.
        """
        if not (
            self._is_running and self._thread is not None and self._thread.is_alive()
        ):
            return False
        if not self._active_edges:
            # Nothing to capture yet - idle is the correct state.
            return True
        if self._capture_blocked:
            # Refused, not broken. ``Server._enable_mouse_stream`` restarts the
            # listener whenever this reports False, which would re-open the
            # permission dialog on a loop and undo the whole point of standing
            # down. The refusal is already reported through ``on_state``.
            return True
        return self._has_session or self._reconnect_pending

    def _notify_state(self, healthy: bool, reason: str | None = None):
        if self._on_state is None:
            return
        try:
            self._on_state(healthy, reason)
        except Exception as exc:
            self._logger.debug(f"on_state callback failed: {exc}")

    def _interruptible_sleep(self, seconds: float) -> bool:
        """Sleep in slices, aborting on stop(). ``False`` means "stop now"."""
        deadline = time.monotonic() + seconds
        while self._is_running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(self._SLEEP_SLICE, remaining))
        return False

    def update_clients(self, state):
        """Update which edges lead to a client.

        ``state`` is ``{"edges": {edge: True, ...}}``; a bare ``{edge: True}``
        mapping is also accepted. This never touches the compositor - the
        barriers were armed once at session setup and stay put. It updates the
        Python-side filter, and it is what tells a stood-down listener that the
        user is asking for capture again.
        """
        edges = state.get("edges") if "edges" in state else state
        self._cmd_queue.put({"type": "update_clients", "clients": edges or {}})

    @property
    def capture_blocked(self) -> bool:
        """Whether the listener has stood down waiting on the user.

        Distinct from "unhealthy": nothing is being retried, so a reporter that
        treats it as a transient outage says the wrong thing.
        """
        return self._capture_blocked

    def request_capture(self):
        """Ask for capture again, forgetting any standing refusal.

        The explicit way back from the stand-down. ``update_clients`` only
        clears a refusal when the edge set actually *changed*, so reconnecting
        the same client onto the same edge - the obvious thing to try after
        cancelling the dialog by mistake - left capture off with no way back
        short of restarting the daemon.
        """
        self._cmd_queue.put({"type": "retry_capture"})

    @property
    def current_activation_id(self) -> int:
        """Id of the activation last handed to ``on_barrier``, or 0.

        Published just before the callback fires and read cross-thread on purpose
        (a plain int, atomic under the GIL): it lets the callback side key
        per-activation state - notably "already logged this one" - without
        threading the id through every callback signature.
        """
        return self._current_activation_id

    def disable_capture(self, x=-1, y=-1, suppress_recapture: bool = False):
        """Release capture.

        ``suppress_recapture`` should be set only when the release *repositions*
        the cursor (the client handing control back): the landing sits close to
        the barrier, so the recapture that follows is spurious and must be
        swallowed. For a plain release with no reposition - rejecting an
        activation, or an active client disconnecting - swallowing the next
        activation would eat a legitimate crossing instead.
        """
        self._cmd_queue.put(
            {
                "type": "disable_capture",
                "x": x,
                "y": y,
                "suppress_recapture": suppress_recapture,
            }
        )

    def _thread_main(self):
        """Session lifecycle loop: idle (no session) or active (poll EIS)."""
        logger = self._logger
        session: _CaptureSession | None = None
        first_session = True
        info = _capture_backend_info()
        logger.info(
            "Wayland capture backend "
            f"module={info.get('module')} version={info.get('version')} "
            f"shared_runtime={info.get('shared_runtime')}"
            + (f" probe_error={info['error']}" if info.get("error") else "")
        )
        if info.get("shared_runtime") is False:
            # Not a nicety: with this build the permission dialog does not come
            # back after the user cancels it once, and no amount of scheduling on
            # this side changes that. Say so where the failure will be looked for.
            logger.error(
                "Wayland capture is running on a pyinputcapture older than "
                f"{_REQUIRED_EXTENSION_VERSION} (no "
                f"{_REQUIRED_EXTENSION_ATTR!r} on InputCapturePortal): it shuts "
                "its tokio runtime down with every portal object, which kills "
                "ashpd's process-global D-Bus connection - so once the input "
                "capture permission dialog is cancelled or ignored, no later "
                "request is ever answered and the dialog never reappears. "
                f"Rebuild and reinstall pyinputcapture (loaded from "
                f"{info.get('module')}, reported version {info.get('version')})"
            )
        saved_stderr = _suppress_libei_stderr()

        try:
            while self._is_running:
                if session is None:
                    result = self._idle_wait(logger)
                    if result is None:
                        break  # quit
                    if result is _NO_SESSION:
                        continue
                    session = result
                    if first_session:
                        self._ready_event.set()
                        first_session = False
                    continue

                action = self._active_tick(session, logger)
                if action == "quit":
                    session.teardown()
                    break
                elif action == "disconnected":
                    session.teardown()  # no-op when the session is dead
                    session = None
                    self._has_session = False
                    self._clients_active = False
                    # Hand back to the idle phase, which owns the retry
                    # schedule. Reconnecting inline is what used to dead-end:
                    # three quick attempts and then nothing, forever, because
                    # only a *new* update_clients could build a session.
                    self._schedule_reconnect(logger, "EIS disconnected")

        except Exception as exc:
            logger.error(f"InputCapture thread fatal: {exc}")
            self._ready_event.set()
            self._has_session = False
            self._reconnect_pending = False
            self._notify_state(False, f"capture thread died: {exc}")
        finally:
            if session is not None:
                session.teardown()
            self._has_session = False
            _restore_stderr(saved_stderr)
            logger.debug("Thread exiting")

    def _schedule_reconnect(self, logger, reason: str, delay: float | None = None):
        """Arm the next reconnect attempt, or stand down if nothing to capture.

        ``delay`` overrides the backoff for failures whose retry cadence is known
        to be wrong to escalate from - see the unauthorised case.
        """
        if not self._active_edges:
            self._reconnect_pending = False
            self._notify_state(True, None)
            return
        if delay is None:
            delay = self._backoff.get_next_delay()
        now = time.monotonic()
        self._reconnect_at = now + delay
        self._reconnect_pending = True
        # The deadline, not just the delay: "reconnecting in 30.0s" followed by a
        # retry two seconds later is what hid the real cadence for a whole round
        # of debugging, and from the delay alone the two are indistinguishable.
        logger.warning(
            f"{reason}; reconnecting in {delay:.1f}s "
            f"(at monotonic {self._reconnect_at:.1f}, now {now:.1f})"
        )
        self._notify_state(False, reason)

    # idle phase (no session)
    def _idle_wait(self, logger):
        """Wait for commands, or retry the session, while none exists."""
        try:
            cmd = self._cmd_queue.get(timeout=0.1)
        except queue.Empty:
            return self._maybe_reconnect(logger)

        cmd_type = cmd.get("type")

        if cmd_type == "update_clients":
            previous = self._active_edges
            new_edges = set(k for k, v in (cmd.get("clients") or {}).items() if v)
            self._active_edges = new_edges
            if new_edges:
                # A fresh client is a good reason to stop waiting out a
                # backoff: the user is asking for capture right now. A *changed*
                # edge set is also the only way back from having stood down on
                # repeated refusals - connecting a client or editing the layout
                # is an explicit request, so it is worth one more dialog.
                if new_edges != previous:
                    self._clear_refusal()
                self._backoff.reset()
                self._reconnect_at = 0.0
                if self._capture_blocked:
                    return _NO_SESSION
                return self._open_session(logger, trigger="update_clients")
            self._reconnect_pending = False
            self._notify_state(True, None)
            return _NO_SESSION

        if cmd_type == "retry_capture":
            # An explicit request for capture: worth one more dialog whatever
            # the standing state is.
            self._clear_refusal()
            self._backoff.reset()
            self._reconnect_at = 0.0
            if not self._active_edges:
                return _NO_SESSION
            return self._open_session(logger, trigger="request_capture")

        if cmd_type == "quit":
            return None

        return _NO_SESSION

    def _clear_refusal(self):
        """Forget a standing refusal so the portal may be asked again."""
        self._capture_blocked = False
        self._needs_user_attempts = 0
        self._unauthorised = None

    def _maybe_reconnect(self, logger):
        """Retry session creation once the backoff deadline has passed."""
        if not self._active_edges or self._capture_blocked:
            return _NO_SESSION
        if time.monotonic() < self._reconnect_at:
            return _NO_SESSION
        return self._open_session(logger, trigger="schedule")

    def _open_session(self, logger, trigger: str = "unknown"):
        """Create a session, or arm the next retry.

        Returns the session or ``_NO_SESSION`` - never a bare failure, so
        capture keeps trying for as long as there is a client to capture for
        and the user has not refused it outright.
        """
        session = self._create_session_once(logger, trigger=trigger)
        if session is None:
            if self._capture_blocked:
                # Standing down: no deadline, nothing pending. Only an explicit
                # user action gets us out of this (see ``_idle_wait``).
                self._reconnect_pending = False
                self._notify_state(False, self._unauthorised)
            elif self._unauthorised is not None:
                # The long delay is right here: only the user can change the
                # answer, and re-asking sooner would just queue portal requests
                # behind one that is still unanswered.
                self._schedule_reconnect(
                    logger,
                    self._unauthorised,
                    delay=self._DIALOG_RETRY_DELAY,
                )
            else:
                self._schedule_reconnect(logger, "capture session unavailable")
            return _NO_SESSION
        self._backoff.reset()
        self._reconnect_pending = False
        self._has_session = True
        self._clients_active = True
        self._notify_state(True, None)
        return session

    def _create_session_once(self, logger, trigger: str = "unknown"):
        """One attempt at creating a session. Retrying is the caller's job.

        Deliberately a single attempt: the inner fast-retry loop this replaced
        predates the backoff schedule in ``_schedule_reconnect`` and duplicated
        it, so a failure produced three ``CreateSession`` requests a second apart
        - the worst possible cadence for a request that needs a human to answer a
        dialog. One attempt per scheduling cycle, backoff between cycles.

        The whole attempt runs under ``_CREATE_LOCK``, and an attempt that cannot
        take it is dropped rather than queued: two dialogs on screen at once is
        the one outcome no schedule can be allowed to produce.
        """
        if not self._is_running:
            return None
        if not _CREATE_LOCK.acquire(blocking=False):
            # Another attempt is inside setup() right now - possibly the previous
            # capture thread of a restarted service, which this object knows
            # nothing about. Returning without touching the refusal state or the
            # attempt count: nothing was asked of the user.
            logger.debug(
                f"[PORTAL_ATTEMPT] skipped trigger={trigger} "
                "(a capture session request is already in flight)"
            )
            return None
        try:
            now = time.monotonic()
            self._attempts += 1
            since_last = (
                f"{now - self._last_attempt_at:.1f}" if self._last_attempt_at else "-"
            )
            self._last_attempt_at = now
            logger.debug(
                f"[PORTAL_ATTEMPT] n={self._attempts} since_last={since_last}s "
                f"trigger={trigger}"
            )
            return self._attempt_create(logger)
        finally:
            _CREATE_LOCK.release()

    def _attempt_create(self, logger):
        """The attempt itself, already counted and serialised by the caller."""
        try:
            session = _CaptureSession.create(
                logger,
                keep_waiting=lambda: self._is_running,
            )
        except _PortalDialogUnanswered as exc:
            self._unauthorised = (
                "no answer to the Wayland input capture permission dialog"
            )
            self._count_needs_user(logger, str(exc), refused=False)
            return None
        except _PortalNotAuthorised as exc:
            # No point retrying: the answer stays "no" until the user acts.
            self._unauthorised = "Wayland input capture was refused"
            self._count_needs_user(logger, str(exc), refused=True)
            return None
        if session is None:
            # Not a permission problem - clear any stale refusal, otherwise the
            # first denial makes every later unrelated failure report itself as
            # "not authorised" and wait out the 30 s ceiling forever.
            self._clear_refusal()
            return None
        self._clear_refusal()
        return session

    def _count_needs_user(self, logger, detail: str, refused: bool):
        """Count one dialog-class failure and latch the stand-down at the cap."""
        self._needs_user_attempts += 1
        what = "was refused" if refused else "got no answer to its permission dialog"
        if self._needs_user_attempts >= self._MAX_NEEDS_USER_ATTEMPTS:
            # Every attempt is another permission dialog in the user's face. Say
            # so once and stop; connecting a client, editing the layout or
            # re-enabling the mouse stream will ask again.
            self._capture_blocked = True
            logger.error(
                f"Wayland input capture {what} {self._needs_user_attempts} times; "
                "no longer asking. Allow input capture in the system dialog, then "
                f"reconnect a client to try again ({detail})"
            )
        else:
            logger.error(
                f"Wayland input capture {what}; allow input capture in the system "
                f"dialog to share input with a client ({detail})"
            )

    # active phase (session exists)
    def _active_tick(self, session, logger):
        """Single iteration of the active session loop."""
        self._dispatch_pending_activation(session)

        # Drain commands
        action = self._process_commands(session, logger)
        if action != "continue":
            return action

        # Poll EIS events (10ms timeout)
        events = session.poller.poll(10)
        if not events:
            return "continue"

        try:
            session.receiver.dispatch()
        except Exception as exc:
            logger.error(f"Receiver dispatch error: {exc}")
            return "continue"

        for event in session.receiver.events:
            result = self._handle_ei_event(event, session, logger)
            if result == "disconnected":
                return "disconnected"

        return "continue"

    def _dispatch_pending_activation(self, session: _CaptureSession):
        """Resolve a pending Activated signal from the D-Bus queue."""
        if not session.pending_activation:
            return

        activation = session.poll_activated()
        if activation:
            bid, cx, cy = activation
            session.pending_activation = False

            # Spurious recapture guard
            if session.ignore_next_activation:
                session.ignore_next_activation = False
                self._logger.debug(
                    "[PENDING_ACTIVATION] IGNORED (spurious recapture after release)"
                )
                try:
                    session.release_cursor(None, None)
                except RuntimeError:
                    pass
                return

            edge = session.barrier_map.get(bid)
            self._logger.debug(
                f"[PENDING_ACTIVATION] bid={bid} edge={edge} cx={cx} cy={cy} "
                f"last_aid={session.last_activation_id}"
            )

            if not self._clients_active or edge not in self._active_edges:
                self._logger.debug(
                    f"[PENDING_ACTIVATION] REJECTED bid={bid} "
                    f"clients_active={self._clients_active} edge={edge} "
                    f"active_edges={self._active_edges} "
                    f"barrier_map={sorted(session.barrier_map)}"
                )
                try:
                    session.release_cursor(None, None)
                except RuntimeError:
                    pass
                return

            if edge and self._on_barrier:
                self._logger.debug(
                    f"[PENDING_ACTIVATION] -> on_barrier({edge}, {cx}, {cy})"
                )
                self._current_activation_id = session.last_activation_id
                self._on_barrier(edge, cx, cy)

    def _process_commands(self, session, logger):
        """Drain the command queue (non-blocking)."""
        while True:
            try:
                cmd = self._cmd_queue.get_nowait()
            except queue.Empty:
                return "continue"

            cmd_type = cmd.get("type")

            if cmd_type == "update_clients":
                new_edges = set(k for k, v in (cmd.get("clients") or {}).items() if v)
                self._active_edges = new_edges
                self._clients_active = bool(new_edges)
                # No portal call. The barriers were armed on all four edges at
                # setup and stay there; a connect/disconnect, a layout edit or a
                # monitor hotplug only changes which activations are acted on
                # (``_dispatch_pending_activation``, then the binding's axis
                # range in ``_resolve_cross_screen_target``).
                logger.debug(f"[EDGES] active={sorted(new_edges)}")

            elif cmd_type == "disable_capture":
                if session.captured:
                    cx, cy = _CaptureSession.compute_release_pos(
                        cmd,
                        session.portal,
                    )
                    logger.debug(f"[CMD] release_cursor abs=({cx}, {cy})")
                    # Only a release that *repositions* the cursor near a barrier
                    # can provoke a spurious recapture worth swallowing. Arming
                    # this for every release let a plain reject eat the next
                    # legitimate crossing, which then released and re-captured -
                    # a capture/release ping-pong that kept the pointer pinned to
                    # the barrier instead of letting it reach the border.
                    if cmd.get("suppress_recapture"):
                        session.ignore_next_activation = True
                    try:
                        session.release_cursor(cx, cy)
                    except RuntimeError as exc:
                        logger.error(str(exc))

            elif cmd_type == "quit":
                self._is_running = False
                return "quit"

        return "continue"

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
                    if (dx or dy) and self._on_move:
                        self._on_move(dx, dy)

            elif etype == EventType.DEVICE_START_EMULATING:
                self._handle_start_emulating(session, logger)

            elif etype == EventType.DEVICE_STOP_EMULATING:
                session.captured = False
                session.pending_activation = False

            elif etype == EventType.BUTTON_BUTTON:
                if session.captured:
                    be = event.button_event
                    btn = _LINUX_BTN_TO_MAPPING.get(be.button)
                    if btn is not None and self._on_click:
                        self._on_click(btn, be.is_press)

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
                    if (dx or dy) and self._on_scroll:
                        self._on_scroll(dx, dy)

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
                session.captured = False
                session.pending_activation = False
                try:
                    session.portal.close()
                except Exception:
                    pass
                # Past this point every portal call would block on a reply
                # that can't come - and it blocks holding the GIL.
                session.dead = True
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
        if (clicks_x or clicks_y) and self._on_scroll:
            self._on_scroll(clicks_x, clicks_y)

    def _handle_start_emulating(self, session: _CaptureSession, logger):
        """Resolve barrier activation on DEVICE_START_EMULATING."""
        session.captured = True
        logger.debug(f"[EIS] START_EMULATING last_aid={session.last_activation_id}")

        # Consume the activation FIRST, even if we are about to ignore this event.
        # Returning before this left ``last_activation_id`` behind the portal's
        # counter, so the *next* resolution replayed the coordinates of the
        # activation we skipped - observed as a cursor position frozen for dozens
        # of capture/release cycles while the user was actually moving, and every
        # routing decision taken against that stale point.
        # ``_dispatch_pending_activation`` has always polled first; this matches it.
        activation = session.poll_activated()

        if session.ignore_next_activation:
            session.ignore_next_activation = False
            logger.debug("[START_EMUL] IGNORED (spurious recapture after release)")
            try:
                session.release_cursor(None, None)
            except RuntimeError as exc:
                logger.error(str(exc))
            return

        if activation:
            bid, cx, cy = activation
            edge = session.barrier_map.get(bid)
            if not self._clients_active or edge not in self._active_edges:
                # This branch used to release with no log output at all, so a
                # barrier id the map doesn't know - the signature of a barrier
                # set that was replaced behind our back - was invisible.
                logger.debug(
                    f"[START_EMUL] REJECTED bid={bid} edge={edge} "
                    f"clients_active={self._clients_active} "
                    f"active_edges={self._active_edges} "
                    f"barrier_map={sorted(session.barrier_map)}"
                )
                try:
                    session.release_cursor(None, None)
                except RuntimeError as exc:
                    logger.error(str(exc))
                return

            if edge and self._on_barrier:
                self._current_activation_id = session.last_activation_id
                self._on_barrier(edge, cx, cy)
        else:
            session.pending_activation = True


__all__ = ["MouseListener", "MouseController", "Button"]
