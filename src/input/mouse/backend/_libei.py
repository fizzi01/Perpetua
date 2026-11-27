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
(pyinputcapture, with a persistent grant) to emulate pointer input via libei.

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

from input._portal import (
    REMOTE_DESKTOP,
    ensure_host_app_registered,
    token_store,
)
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


def _event_type(event) -> EventType | None:
    """The event's type, or None for one snegg's enum doesn't know.

    libei >= 1.x sends EI_EVENT_PONG/EI_EVENT_SYNC (90/91), on which
    ``event.event_type`` raises ValueError (seen on GNOME 50 / libei 1.5).
    """
    try:
        return event.event_type
    except ValueError:
        return None


#: fd 2 is process-wide, so the redirect is reference-counted: the first holder
#: saves the real stderr and only the last one restores it, whatever order the
#: capture threads exit in.
_STDERR_LOCK = threading.Lock()
_stderr_holders = 0
_stderr_saved: int | None = None


def _suppress_libei_stderr() -> bool:
    """Point fd 2 at /dev/null to silence libei's C-level debug spam.

    Returns whether this call holds the redirect (pass it to ``_restore_stderr``).
    """
    global _stderr_holders, _stderr_saved
    with _STDERR_LOCK:
        if _stderr_holders == 0:
            try:
                devnull = os.open(os.devnull, os.O_WRONLY)
                saved = os.dup(2)
                os.dup2(devnull, 2)
                os.close(devnull)
            except OSError:
                return False
            _stderr_saved = saved
        _stderr_holders += 1
        return True


def _restore_stderr(held: bool) -> None:
    global _stderr_holders, _stderr_saved
    if not held:
        return
    with _STDERR_LOCK:
        _stderr_holders -= 1
        if _stderr_holders or _stderr_saved is None:
            return
        try:
            os.dup2(_stderr_saved, 2)
            os.close(_stderr_saved)
        except OSError:
            pass
        _stderr_saved = None


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
    """The extension's account of a failed setup.

    The only place a late task error survives: the capture thread points fd 2
    at /dev/null for its whole lifetime.
    """
    if portal is None:
        return None
    detail = portal.last_error
    return str(detail).strip() or None if detail else None


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
        # The compositor offers relative and absolute pointers as separate
        # devices; warps go to the absolute one, in its regions' space.
        self._abs_device = None
        self.regions: list[tuple[int, int, int, int, float]] = []
        self._sender: Sender | None = None
        self._portal = None
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
    def abs_device(self):
        with _conn_lock:
            if self._error is not None:
                raise self._error
            return self._abs_device

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
        is refcounted and destroyed when the last Python reference drops. So
        the teardown is about *ordering*: stop the dispatch thread, close the
        device, drop the sender, then close the portal session.
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
        portal, self._portal = self._portal, None
        if portal is not None:
            try:
                portal.close()
            except Exception:
                pass

    def abort(self) -> None:
        """Give up a ``connect()`` in flight, from any thread.

        Closing the portal ends a wait on the dialog and takes the dialog down.
        """
        self._closing = True
        portal = self._portal
        if portal is not None:
            try:
                portal.close()
            except Exception:
                pass

    def connect(self, consent_timeout: float = 300.0, on_dialog=None):
        """``on_dialog`` is called when the portal is about to ask the user."""
        if self._device is not None:
            return

        eis_fd = self._open_portal_session(consent_timeout, on_dialog)

        poller = _select.poll()

        # Libei Sender
        self._sender = _ei_from_fd(Sender, eis_fd, "perpetua-mouse-controller")
        poller.register(self._sender.fd, _select.POLLIN)

        # Seat bind and device acquisition loop. Take one relative and one
        # absolute pointer; stop once both are resumed, or once the device
        # list has been quiet for a while (a compositor may offer only one).
        seat_bound = False
        rel = absolute = None
        resumed: set[int] = set()
        quiet = 0
        for _ in range(100):
            got = False
            if poller.poll(100):
                self._sender.dispatch()
            for event in self._sender.events:
                got = True
                etype = _event_type(event)
                if etype == EventType.SEAT_ADDED and not seat_bound:
                    event.seat.bind(_CONTROLLER_CAPABILITIES)
                    seat_bound = True
                elif etype == EventType.DEVICE_ADDED:
                    dev = event.device
                    caps = dev.capabilities
                    if absolute is None and DeviceCapability.POINTER_ABSOLUTE in caps:
                        absolute = dev
                        self.regions = _device_regions(dev)
                    elif rel is None and DeviceCapability.POINTER in caps:
                        rel = dev
                    else:
                        continue
                    dev.start_emulating(0)
                elif etype == EventType.DEVICE_RESUMED:
                    resumed.add(id(event.device))
            wanted = [d for d in (rel, absolute) if d is not None]
            done = [d for d in wanted if id(d) in resumed]
            if wanted and len(done) == len(wanted):
                quiet = 0 if got else quiet + 1
                if (rel is not None and absolute is not None) or quiet >= 5:
                    self._adopt(rel, absolute)
                    return

        raise RuntimeError("libei: no device received after seat bind")

    def _open_portal_session(self, consent_timeout: float, on_dialog=None) -> int:
        """The EIS fd of a granted session; silent when the saved token holds.

        The portal waits for a human, so the wait is bounded by
        ``consent_timeout``. The token is single use: the new one is saved
        straight away, and a refusal forgets it.
        """
        from pyinputcapture import RemoteDesktopPortal

        registered = ensure_host_app_registered()
        store = token_store()
        token = store.load(REMOTE_DESKTOP, registered)

        portal = RemoteDesktopPortal()
        self._portal = portal
        if self._closing:
            raise RuntimeError("libei: connect aborted")

        if token is None and on_dialog is not None:
            # Without a token the portal always shows its dialog.
            on_dialog()
        started = time.monotonic()
        try:
            eis_fd, new_token = portal.setup(
                restore_token=token, persist=True, timeout=consent_timeout
            )
        except PermissionError as exc:
            store.clear(REMOTE_DESKTOP)
            raise _ConsentRefused("Remote input permission was denied") from exc
        except TimeoutError as exc:
            raise _ConsentTimeout(
                "The remote input permission dialog was not answered in time"
            ) from exc
        if self._closing:
            raise RuntimeError("libei: connect aborted")

        if new_token:
            store.save(REMOTE_DESKTOP, new_token, registered)
        get_logger("_EiConnection").info(
            "RemoteDesktop session granted",
            had_token=token is not None,
            token_saved=bool(new_token),
            wait_s=round(time.monotonic() - started, 2),
        )
        return eis_fd

    def _adopt(self, rel, absolute) -> None:
        primary = rel if rel is not None else absolute
        caps = primary.capabilities
        self._has_pointer = DeviceCapability.POINTER in caps
        self._has_pointer_abs = DeviceCapability.POINTER_ABSOLUTE in caps
        self._abs_device = absolute
        self._device = primary
        self._start_dispatch()

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
                    etype = _event_type(event)

                    if etype == EventType.DEVICE_PAUSED:
                        self._paused.set()

                    elif etype == EventType.DEVICE_RESUMED:
                        self._paused.clear()
                        for dev in (self._device, self._abs_device):
                            if dev is not None:
                                dev.start_emulating(0)

                    elif etype == EventType.DEVICE_REMOVED:
                        # Null the device and set the error atomically so the
                        # controller's `device` property never observes a live
                        # None (which would raise an opaque 'NoneType' error
                        # instead of this reason).
                        with _conn_lock:
                            self._device = None
                            self._abs_device = None
                            self._error = RuntimeError(
                                "libei: device removed by compositor"
                            )
                        return

                    elif etype == EventType.DISCONNECT:
                        with _conn_lock:
                            self._device = None
                            self._abs_device = None
                            self._error = RuntimeError(
                                "libei: disconnected by compositor"
                            )
                        return
        finally:
            _restore_stderr(saved_stderr)


_conn_lock = threading.Lock()


class _ConsentRefused(RuntimeError):
    """The portal closed the session before granting it: the user said no."""


class _ConsentTimeout(RuntimeError):
    """Nobody answered the permission dialog in time."""


class _LinkState(enum.Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    ACTIVE = "active"
    DENIED = "denied"


class _RemoteDesktopLink:
    """The client's one RemoteDesktop session.

    Every new session is a consent dialog, possibly on a machine with no
    peripherals, so: the session is opened off the caller's thread (the wait
    is a human answering), kept for the daemon's lifetime, and given up on
    (``DENIED``) as soon as only the user can fix it - a refusal, an
    unanswered dialog, or ``MAX_TRANSIENT_FAILURES`` in a row. ``DENIED``
    asks nothing more until ``reset()``, which only an explicit user action
    (a new client connection) may call. Failure listeners hear it once.
    Consent listeners hear ``CONSENT_PENDING`` when the portal is about to
    show its dialog, and ``CONSENT_GRANTED`` when that dialog was accepted.
    """

    CONSENT_PENDING = "pending"
    CONSENT_GRANTED = "granted"

    MAX_TRANSIENT_FAILURES = 5
    CONSENT_TIMEOUT = 300.0
    _RETRY_MIN = 1.0
    _RETRY_MAX = 30.0

    def __init__(self, connection_factory=None):
        self._factory = connection_factory or _EiConnection
        self._lock = threading.Lock()
        self._state = _LinkState.IDLE
        self._conn: _EiConnection | None = None
        self._pending: _EiConnection | None = None
        self._generation = 0
        self._transient_failures = 0
        self._retry_at = 0.0
        self._backoff = ExponentialBackoff(
            initial_delay=self._RETRY_MIN, max_delay=self._RETRY_MAX
        )
        self.denied_reason: str | None = None
        self.awaiting_consent = False
        self._listeners: list = []
        self._consent_listeners: list = []
        self._logger = get_logger(self.__class__.__name__)

    @property
    def state(self) -> _LinkState:
        return self._state

    def add_failure_listener(self, callback) -> None:
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_failure_listener(self, callback) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def add_consent_listener(self, callback) -> None:
        with self._lock:
            if callback not in self._consent_listeners:
                self._consent_listeners.append(callback)

    def remove_consent_listener(self, callback) -> None:
        with self._lock:
            if callback in self._consent_listeners:
                self._consent_listeners.remove(callback)

    def _notify_consent(self, generation: int, state: str) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self.awaiting_consent = state == self.CONSENT_PENDING
            listeners = list(self._consent_listeners)
        self._logger.info("RemoteDesktop consent", state=state)
        for callback in listeners:
            try:
                callback(state)
            except Exception as exc:
                self._logger.debug("consent listener raised", error=str(exc))

    def connection(self) -> "_EiConnection | None":
        """The live connection, or None. Never blocks.

        Starts an attempt in the background when none is running and one is
        due; a session the compositor dropped counts as due immediately.
        """
        stale = None
        with self._lock:
            conn = self._conn
            if self._state is _LinkState.ACTIVE:
                if (
                    conn is not None
                    and conn._error is None
                    and conn._device is not None
                ):
                    return conn
                self._logger.warning(
                    "RemoteDesktop session lost",
                    reason=str(conn._error) if conn is not None else None,
                )
                stale, self._conn = conn, None
                self._state = _LinkState.IDLE
            due = self._state is _LinkState.IDLE and time.monotonic() >= self._retry_at
            if due:
                self._state = _LinkState.CONNECTING
                generation = self._generation
        if stale is not None:
            _close_quietly(stale)
        if due:
            threading.Thread(
                target=self._attempt,
                args=(generation,),
                name="libei-remote-desktop-connect",
                daemon=True,
            ).start()
        return None

    def _attempt(self, generation: int) -> None:
        conn = self._factory()
        with self._lock:
            if generation != self._generation:
                return
            self._pending = conn
        self._logger.info("Requesting RemoteDesktop session")
        asked = False

        def on_dialog():
            nonlocal asked
            asked = True
            self._notify_consent(generation, self.CONSENT_PENDING)

        try:
            conn.connect(consent_timeout=self.CONSENT_TIMEOUT, on_dialog=on_dialog)
        except (_ConsentRefused, _ConsentTimeout) as exc:
            _close_quietly(conn)
            cause = exc.__cause__
            self._deny(
                generation,
                str(exc),
                detail=_describe(cause) if cause is not None else None,
            )
            return
        except Exception as exc:
            _close_quietly(conn)
            with self._lock:
                if generation != self._generation:
                    return
                self._pending = None
                self.awaiting_consent = False
                self._transient_failures += 1
                failures = self._transient_failures
                if failures < self.MAX_TRANSIENT_FAILURES:
                    delay = self._backoff.get_next_delay()
                    self._retry_at = time.monotonic() + delay
                    self._state = _LinkState.IDLE
            if failures < self.MAX_TRANSIENT_FAILURES:
                self._logger.warning(
                    "RemoteDesktop session failed, will retry",
                    error=_describe(exc),
                    attempt=failures,
                    retry_in_s=round(delay, 1),
                )
                return
            self._deny(
                generation,
                f"Could not open a remote input session after {failures} attempts",
                detail=_describe(exc),
            )
            return

        with self._lock:
            if generation != self._generation:
                stale = conn
            else:
                stale = None
                self._pending = None
                self.awaiting_consent = False
                self._conn = conn
                self._state = _LinkState.ACTIVE
                self._transient_failures = 0
                self._backoff.reset()
        if stale is not None:
            _close_quietly(stale)
            return
        self._logger.info("RemoteDesktop session active")
        if asked:
            self._notify_consent(generation, self.CONSENT_GRANTED)

    def _deny(self, generation: int, reason: str, detail: str | None = None) -> None:
        """``reason`` is shown to the user as is; ``detail`` only reaches the log."""
        with self._lock:
            if generation != self._generation:
                return
            self._pending = None
            self._state = _LinkState.DENIED
            self.denied_reason = reason
            self.awaiting_consent = False
            listeners = list(self._listeners)
        self._logger.warning(
            "RemoteDesktop input injection unavailable", reason=reason, detail=detail
        )
        for callback in listeners:
            try:
                callback(reason)
            except Exception as exc:
                self._logger.debug("failure listener raised", error=str(exc))

    def current_regions(self) -> list[tuple[int, int, int, int, float]]:
        with self._lock:
            conn = self._conn if self._state is _LinkState.ACTIVE else None
        return list(conn.regions) if conn is not None else []

    def reset(self) -> None:
        """Forget a denial so the next ``connection()`` asks again."""
        with self._lock:
            if self._state is not _LinkState.DENIED:
                return
            self._state = _LinkState.IDLE
            self._transient_failures = 0
            self._retry_at = 0.0
            self._backoff.reset()
            self.denied_reason = None

    def close(self) -> None:
        """Close the session and abandon any attempt in flight."""
        with self._lock:
            self._generation += 1
            self.awaiting_consent = False
            conn, self._conn = self._conn, None
            pending, self._pending = self._pending, None
            if self._state is not _LinkState.DENIED:
                self._state = _LinkState.IDLE
            self._retry_at = 0.0
        if pending is not None:
            pending.abort()
        if conn is not None:
            _close_quietly(conn)


def _close_quietly(conn: "_EiConnection") -> None:
    try:
        conn.close()
    except Exception:
        pass


def _describe(exc: BaseException) -> str:
    # snegg raises some errors with an empty or None message.
    text = str(exc)
    return text if text and text != "None" else type(exc).__name__


_link = _RemoteDesktopLink()


def remote_desktop_link() -> _RemoteDesktopLink:
    return _link


def shutdown_connection() -> None:
    """Close the module-wide RemoteDesktop connection, if any."""
    _link.close()


def _device_regions(device) -> list[tuple[int, int, int, int, float]]:
    """``(x, y, w, h, physical_scale)`` per region, in logical pixels.

    The space ``pointer_motion_absolute`` takes, scaling already applied: the
    right geometry for the client's monitors, unlike ``wl_output`` modes.
    """
    regions, idx = [], 0
    while True:
        r = libei.device_get_region(device._cobject, idx)
        if not r:
            return regions
        regions.append(
            (
                int(libei.region_get_x(r)),
                int(libei.region_get_y(r)),
                int(libei.region_get_width(r)),
                int(libei.region_get_height(r)),
                float(libei.region_get_physical_scale(r)),
            )
        )
        idx += 1


def remote_desktop_regions() -> list[tuple[int, int, int, int, float]]:
    """Regions of the live RemoteDesktop session, or [] (never starts one)."""
    return _link.current_regions()


def _scroll_discrete(device, dx: int, dy: int):
    libei.device_scroll_discrete(device._cobject, dx, dy)


class MouseController:
    """Emulates pointer input through the RemoteDesktop portal via libei."""

    def __init__(self):
        self._x = 0
        self._y = 0
        self._link = _link
        # Starts the session in the background; input arriving before it is
        # granted is dropped, never queued and never blocking the caller.
        self._link.connection()

    def _live(self) -> "_EiConnection | None":
        conn = self._link.connection()
        if conn is None or conn.paused:
            return None
        return conn

    @property
    def position(self) -> tuple[int, int]:
        return (self._x, self._y)

    @position.setter
    def position(self, value: tuple[int, int]):
        x, y = int(value[0]), int(value[1])
        conn = self._live()
        if conn is not None:
            absolute = conn.abs_device
            if absolute is not None:
                absolute.pointer_motion_absolute(float(x), float(y))
                absolute.frame(_now_us())
            else:
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
        conn = self._live()
        if conn is not None:
            device = conn.device
            if conn._has_pointer:
                device.pointer_motion(float(dx), float(dy))
            else:
                device.pointer_motion_absolute(float(self._x), float(self._y))
            device.frame(_now_us())

    def press(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        conn = self._live() if code is not None else None
        if conn is not None:
            conn.device.button_button(code, True).frame(_now_us())

    def release(self, button: Button):
        code = ButtonToEcodeMap.get(button.name)
        conn = self._live() if code is not None else None
        if conn is not None:
            conn.device.button_button(code, False).frame(_now_us())

    def click(self, button: Button, count: int = 1):
        for _ in range(count):
            self.press(button)
            self.release(button)

    def scroll(self, dx: int, dy: int):
        if not (dx or dy):
            return
        conn = self._live()
        if conn is not None:
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
        "enabled",
        "seat_bound",
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
        # False only for a session approved after its listener stopped: it was
        # parked before ``enable()``, so no barrier can capture the pointer.
        self.enabled: bool = True
        self.seat_bound: bool = False

    @classmethod
    def create(cls, logger, keep_waiting=None) -> "_CaptureSession | None":
        """Create a portal session with whole-edge barriers on all four sides.

        The *only* place barriers are ever armed; there is deliberately no way
        to change them afterwards (see ``_ALL_EDGES``). A fresh portal object
        every time: the D-Bus connection carrying the request is process-global
        in the extension, so it outlives any one object.
        """
        from pyinputcapture import InputCapturePortal
        from snegg.ei import Receiver

        # Grants are keyed by app id, fixed by the first portal call.
        ensure_host_app_registered()
        portal = None
        try:
            portal = InputCapturePortal()
            zones, eis_fd, bmap_list = cls._setup(portal)
            logger.debug("Capture session created", zones=zones)

            receiver = _ei_from_fd(Receiver, eis_fd, "perpetua-cursor-capture")
            poller = _select.poll()
            poller.register(receiver.fd, _select.POLLIN)
            barrier_map = {bid: edge for bid, edge in bmap_list}
            session = cls(portal, receiver, barrier_map, poller, _ALL_EDGES)

            if keep_waiting is not None and not keep_waiting():
                # Approved after stop(): keep the grant for the next listener,
                # but don't enable barriers nobody would release.
                session.enabled = False
                return session
            session.activate(logger, keep_waiting)
            return session

        except Exception as exc:
            reason = str(exc)
            detail = _portal_last_error(portal)
            if detail and detail not in reason:
                # Often the only thing that classifies the failure at all.
                reason = f"{reason} ({detail})"
            if portal is not None:
                try:
                    portal.close()
                except Exception:
                    pass
            # Both dialog-class failures go to the caller's human-paced
            # schedule; anything else is transient and keeps the backoff.
            if _is_permission_failure(reason):
                raise _PortalNotAuthorised(reason) from exc
            if _is_setup_timeout(reason):
                raise _PortalDialogUnanswered(reason) from exc
            logger.error("Capture session setup failed", reason=reason)
            return None

    def activate(self, logger, keep_waiting=None) -> None:
        """Enable the barriers and bind the EIS seat."""
        self.portal.enable()
        self.enabled = True
        if not self.seat_bound:
            self.seat_bound = _wait_for_seat(self.receiver, logger, keep_waiting)

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


def _wait_for_seat(receiver, logger, keep_waiting=None) -> bool:
    """Wait for the EIS seat and bind capabilities; return whether it was bound.

    ``keep_waiting`` is polled before starting and on every iteration, so a
    ``stop()`` during this 10-second wait aborts it: otherwise the capture
    thread ignores the quit request for long enough to blow through the join
    timeout and outlive the service that owns it.
    """
    if keep_waiting is not None and not keep_waiting():
        return False

    poller = _select.poll()
    poller.register(receiver.fd, _select.POLLIN)

    seat_bound = False
    device_ready = False

    for _ in range(100):  # 10 seconds max
        if keep_waiting is not None and not keep_waiting():
            return seat_bound
        if poller.poll(100):
            receiver.dispatch()

        for event in receiver.events:
            etype = _event_type(event)

            if etype == EventType.SEAT_ADDED:
                event.seat.bind(_LISTENER_CAPABILITIES)
                receiver.dispatch()
                seat_bound = True

            elif etype == EventType.DEVICE_RESUMED:
                device_ready = True

        if seat_bound and device_ready:
            return True

    if not seat_bound:
        logger.warning("EIS seat wait timed out (no SEAT_ADDED)")
    elif not device_ready:
        logger.warning("EIS seat bound but no DEVICE_RESUMED (continuing)")
    return seat_bound


_NO_SESSION = object()  # sentinel: no session yet (distinct from None = quit)
_IN_FLIGHT = object()  # sentinel: another thread holds the dialog; nothing asked

#: ``on_state(True, _IDLE_REASON)``: healthy, but no session because no client
#: is bound yet. A bare ``True`` means a live session.
_IDLE_REASON = "no client bound"


#: Held for one ``_CaptureSession.create``, process-wide: two permission dialogs
#: must never be on screen at once, and a listener rebuilt after ``Server.cleanup()``
#: cannot see a previous thread still inside ``setup()``. Not a time floor.
_CREATE_LOCK = threading.Lock()

#: A session approved after its listener stopped, waiting for the next one, so
#: a late "Allow" is not answered with a second dialog. Taken under _CREATE_LOCK.
_PARK_LOCK = threading.Lock()
_parked_session: "_CaptureSession | None" = None


def _park_session(session: "_CaptureSession") -> None:
    global _parked_session
    with _PARK_LOCK:
        previous, _parked_session = _parked_session, session
    if previous is not None:
        previous.teardown()


def _take_parked_session() -> "_CaptureSession | None":
    global _parked_session
    with _PARK_LOCK:
        session, _parked_session = _parked_session, None
    return session


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
    # How soon to look again when another listener holds the dialog.
    _IN_FLIGHT_RECHECK = 1.0

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
        # start()/stop() run on the loop, a deferred start on its waiter thread.
        self._lifecycle_lock = threading.RLock()
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

        Never runs two capture threads on one EIS receiver: while the previous
        one is alive (``stop()``'s join times out on an unanswered dialog) the
        start is deferred onto a waiter rather than dropped. Two of them not
        raising two dialogs is ``_CREATE_LOCK``'s job, not this one's.

        Returns whether capture is now running or scheduled to.
        """
        with self._lifecycle_lock:
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
        """Start once ``previous`` exits, on a one-shot waiter thread.

        Waits as long as the start is still wanted: the previous thread's own
        wait is bounded (``_SETUP_TIMEOUT`` plus the seat wait), so it does exit.
        Past ``_PREVIOUS_THREAD_WAIT`` the delay is reported, not given up on.
        """
        if self._deferred_start:
            return
        self._deferred_start = True
        self._logger.warning(
            "InputCapture start deferred until the previous capture thread exits",
            reason="previous thread still in portal setup",
        )

        def _wait_and_start():
            deadline = time.monotonic() + self._PREVIOUS_THREAD_WAIT
            reported = False
            try:
                while self._start_wanted and previous.is_alive():
                    previous.join(timeout=self._SLEEP_SLICE * 10)
                    if not reported and time.monotonic() >= deadline:
                        reported = True
                        self._logger.error(
                            "Previous capture thread still blocked; capture waits",
                            waited_s=round(self._PREVIOUS_THREAD_WAIT),
                        )
                        self._notify_state(False, "previous capture thread blocked")
                with self._lifecycle_lock:
                    self._deferred_start = False
                    if not self._start_wanted:
                        # Stopped while we waited: starting now would raise a
                        # dialog after an explicit stop.
                        return
                    self.start()
            finally:
                self._deferred_start = False

        threading.Thread(target=_wait_and_start, daemon=True).start()

    def stop(self):
        """Stop the daemon thread."""
        with self._lifecycle_lock:
            # Before the early return: a stop must also cancel a deferred start.
            self._start_wanted = False
            if not self._is_running:
                return
            self._is_running = False
            thread = self._thread
        self._cmd_queue.put({"type": "quit"})
        if thread and thread.is_alive():
            thread.join(timeout=3)
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
            self._logger.debug("on_state callback failed", error=str(exc))

    def _interruptible_sleep(self, seconds: float) -> bool:
        """Sleep in slices, aborting on stop(). ``False`` means "stop now"."""
        deadline = time.monotonic() + seconds
        while self._is_running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(self._SLEEP_SLICE, remaining))
        return False

    def update_clients(self, state, requested: bool = False):
        """Update which edges lead to a client.

        ``state`` is ``{"edges": {edge: True, ...}}``; a bare ``{edge: True}``
        mapping is also accepted. This never touches the compositor: barriers
        are armed once at session setup. Without a session, ``requested`` (a
        client connecting) or an edge that wasn't bound before is the user
        asking for capture, which forgives a stand-down and asks once now. A
        repeat or a shrink leaves the retry schedule alone.
        """
        edges = state.get("edges") if "edges" in state else state
        self._cmd_queue.put(
            {"type": "update_clients", "clients": edges or {}, "requested": requested}
        )

    @property
    def capture_blocked(self) -> bool:
        """Whether the listener has stood down waiting on the user.

        Distinct from "unhealthy": nothing is being retried, so a reporter that
        treats it as a transient outage says the wrong thing.
        """
        return self._capture_blocked

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
        stderr_held = _suppress_libei_stderr()

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
            logger.error("InputCapture thread fatal", error=str(exc))
            self._ready_event.set()
            self._has_session = False
            self._reconnect_pending = False
            self._notify_state(False, f"capture thread died: {exc}")
        finally:
            if session is not None:
                if session.enabled:
                    session.teardown()
                else:
                    _park_session(session)
                    logger.info(
                        "Capture permission granted after stop; kept for next start"
                    )
            self._has_session = False
            _restore_stderr(stderr_held)

    def _schedule_reconnect(self, logger, reason: str, delay: float | None = None):
        """Arm the next reconnect attempt, or stand down if nothing to capture.

        ``delay`` overrides the backoff for failures whose retry cadence is known
        to be wrong to escalate from - see the unauthorised case.
        """
        if not self._active_edges:
            self._reconnect_pending = False
            self._notify_state(True, _IDLE_REASON)
            return
        if delay is None:
            delay = self._backoff.get_next_delay()
        now = time.monotonic()
        self._reconnect_at = now + delay
        self._reconnect_pending = True
        # The deadline as well as the delay: the two are indistinguishable in the
        # log otherwise, and only the deadline says when the retry really fires.
        logger.warning(
            "Capture session unavailable, will retry",
            reason=reason,
            retry_in_s=round(delay, 1),
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
            if not new_edges:
                self._reconnect_pending = False
                self._notify_state(True, _IDLE_REASON)
                return _NO_SESSION
            if not (cmd.get("requested") or new_edges - previous):
                # Same or fewer edges: layout chatter or another client
                # leaving. Not a request; the schedule already covers it.
                return _NO_SESSION
            self._clear_refusal()
            self._backoff.reset()
            self._reconnect_at = 0.0
            return self._open_session(logger, trigger="update_clients")

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
        if session is _IN_FLIGHT:
            # Another listener's dialog is on screen. Nothing was asked, so
            # nothing failed: look again shortly without reporting an outage.
            self._reconnect_at = time.monotonic() + self._IN_FLIGHT_RECHECK
            self._reconnect_pending = True
            return _NO_SESSION
        if not self._is_running:
            # Stopped while the dialog was open. The thread exits next and
            # parks an approved session rather than tearing it down.
            return session if session is not None else _NO_SESSION
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

        The backoff in ``_schedule_reconnect`` owns the cadence: one attempt per
        scheduling cycle, since every attempt is another permission dialog. An
        attempt that cannot take ``_CREATE_LOCK`` is dropped, not queued.
        """
        if not self._is_running:
            return None
        if not _CREATE_LOCK.acquire(blocking=False):
            # Nothing was asked of the user, so this costs no attempt and
            # leaves the refusal state alone.
            return _IN_FLIGHT
        try:
            parked = self._adopt_parked(logger)
            if parked is not None:
                return parked
            logger.debug("Requesting InputCapture session", trigger=trigger)
            return self._attempt_create(logger)
        finally:
            _CREATE_LOCK.release()

    def _adopt_parked(self, logger) -> "_CaptureSession | None":
        """Take over a session granted after a previous listener stopped."""
        session = _take_parked_session()
        if session is None:
            return None
        try:
            session.activate(logger, keep_waiting=lambda: self._is_running)
        except Exception as exc:
            logger.warning("Parked capture session unusable", error=str(exc))
            session.teardown()
            return None
        logger.info("Reusing capture permission granted after the last stop")
        self._clear_refusal()
        return session

    def _attempt_create(self, logger):
        """The attempt itself, already serialised by the caller."""
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
            # Not a permission problem: report it as such and use the backoff,
            # but keep the refusal count - a flaky compositor between two
            # refusals must not buy a fourth dialog.
            self._unauthorised = None
            return None
        self._clear_refusal()
        return session

    def _count_needs_user(self, logger, detail: str, refused: bool):
        """Count one dialog-class failure and latch the stand-down at the cap."""
        self._needs_user_attempts += 1
        outcome = "refused" if refused else "unanswered"
        if self._needs_user_attempts >= self._MAX_NEEDS_USER_ATTEMPTS:
            # Every attempt is another dialog in the user's face. Stop; a client
            # connecting, or a newly bound edge, asks again (``update_clients``).
            self._capture_blocked = True
            logger.error(
                "Wayland input capture not permitted; no longer asking until a "
                "client connects",
                outcome=outcome,
                attempts=self._needs_user_attempts,
                detail=detail,
            )
        else:
            logger.error(
                "Wayland input capture not permitted; allow it in the system dialog",
                outcome=outcome,
                attempts=self._needs_user_attempts,
                detail=detail,
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
            logger.error("Receiver dispatch error", error=str(exc))
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
                try:
                    session.release_cursor(None, None)
                except RuntimeError:
                    pass
                return

            edge = session.barrier_map.get(bid)
            if not self._clients_active or edge not in self._active_edges:
                self._log_rejected(self._logger, bid, edge, session)
                try:
                    session.release_cursor(None, None)
                except RuntimeError:
                    pass
                return

            if edge and self._on_barrier:
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

            elif cmd_type == "disable_capture":
                if session.captured:
                    cx, cy = _CaptureSession.compute_release_pos(
                        cmd,
                        session.portal,
                    )
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
                        logger.error("Capture release failed", error=str(exc))

            elif cmd_type == "quit":
                self._is_running = False
                return "quit"

        return "continue"

    def _handle_ei_event(self, event, session: _CaptureSession, logger):
        """Process a single EIS event."""
        etype = _event_type(event)
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
                # Past this point every portal call but close() (a non-blocking
                # try_send) would block on a reply that can't come.
                session.dead = True
                try:
                    session.portal.close()
                except Exception:
                    pass
                return "disconnected"

        except Exception as exc:
            logger.error("EI event error", error=str(exc))

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

    def _log_rejected(self, logger, bid, edge, session: _CaptureSession) -> None:
        # An unknown barrier id is the signature of a barrier set replaced
        # behind our back, so the map is logged too.
        logger.debug(
            "Activation not routed",
            barrier_id=bid,
            edge=edge,
            clients_active=self._clients_active,
            active_edges=sorted(self._active_edges),
            barrier_ids=sorted(session.barrier_map),
        )

    def _handle_start_emulating(self, session: _CaptureSession, logger):
        """Resolve barrier activation on DEVICE_START_EMULATING."""
        session.captured = True

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
            try:
                session.release_cursor(None, None)
            except RuntimeError as exc:
                logger.error("Capture release failed", error=str(exc))
            return

        if activation:
            bid, cx, cy = activation
            edge = session.barrier_map.get(bid)
            if not self._clients_active or edge not in self._active_edges:
                self._log_rejected(logger, bid, edge, session)
                try:
                    session.release_cursor(None, None)
                except RuntimeError as exc:
                    logger.error("Capture release failed", error=str(exc))
                return

            if edge and self._on_barrier:
                self._current_activation_id = session.last_activation_id
                self._on_barrier(edge, cx, cy)
        else:
            session.pending_activation = True


__all__ = ["MouseListener", "MouseController", "Button"]
