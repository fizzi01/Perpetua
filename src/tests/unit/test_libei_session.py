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
"""libei capture-session lifecycle, without a compositor.

``_libei`` needs the real ``snegg``/``libei`` shared libraries at import time,
which only exist on a configured Linux box - so the module under test is
imported behind stubs. What is exercised here is the pure Python state machine:
the reconnect schedule (an ``EIS disconnected`` used to dead-end permanently),
the stand-down after repeated refusals, the promise that nothing ever re-arms a
barrier, and the teardown guard that keeps a dead session from making another
blocking portal call.
"""

import os
import sys
import time
import types
from unittest.mock import MagicMock

import pytest


def _install_snegg_stubs(monkeypatch):
    """Minimal stand-ins for the modules ``_libei`` imports at module level."""

    class _EventType:
        POINTER_MOTION = 1
        DEVICE_START_EMULATING = 2
        DEVICE_STOP_EMULATING = 3
        BUTTON_BUTTON = 4
        SCROLL_DISCRETE = 5
        SCROLL_DELTA = 6
        SEAT_ADDED = 7
        DEVICE_ADDED = 8
        DEVICE_PAUSED = 9
        DEVICE_RESUMED = 10
        DEVICE_REMOVED = 11
        DISCONNECT = 12

    class _DeviceCapability:
        POINTER = 1
        POINTER_ABSOLUTE = 2
        BUTTON = 3
        SCROLL = 4
        TOUCH = 5

    class _DeviceType:
        POINTER = 1

    ei = types.ModuleType("snegg.ei")
    ei.Sender = MagicMock()
    ei.Receiver = MagicMock()
    ei.EventType = _EventType
    ei.DeviceCapability = _DeviceCapability

    c_libei = types.ModuleType("snegg.c.libei")
    c_libei.libei = MagicMock()

    oeffis = types.ModuleType("snegg.oeffis")
    oeffis.Oeffis = MagicMock()
    oeffis.DeviceType = _DeviceType
    oeffis.DisconnectedError = type("DisconnectedError", (Exception,), {})
    oeffis.SessionClosedError = type("SessionClosedError", (Exception,), {})

    evdev = types.ModuleType("evdev")
    evdev.ecodes = types.SimpleNamespace(BTN_LEFT=272, BTN_RIGHT=273, BTN_MIDDLE=274)

    for name, mod in (
        ("snegg", types.ModuleType("snegg")),
        ("snegg.ei", ei),
        ("snegg.c", types.ModuleType("snegg.c")),
        ("snegg.c.libei", c_libei),
        ("snegg.oeffis", oeffis),
        ("evdev", evdev),
    ):
        monkeypatch.setitem(sys.modules, name, mod)


@pytest.fixture
def libei(monkeypatch):
    _install_snegg_stubs(monkeypatch)
    monkeypatch.delitem(sys.modules, "input.mouse.backend._libei", raising=False)
    import importlib

    module = importlib.import_module("input.mouse.backend._libei")
    yield module
    sys.modules.pop("input.mouse.backend._libei", None)


@pytest.fixture
def logger():
    return MagicMock()


def _session(libei, portal=None, armed=("right",)):
    portal = portal or MagicMock()
    return libei._CaptureSession(
        portal=portal,
        receiver=MagicMock(),
        barrier_map={1: "right"},
        poller=MagicMock(),
        armed_edges=armed,
    )


def _active_listener(libei):
    """A listener that considers the "right" edge bound, for one-tick drives."""
    listener = libei.MouseListener()
    listener._active_edges = {"right"}
    listener._clients_active = True
    return listener


class TestReleaseIntent:
    """A release that repositions differs from one that merely lets go."""

    def test_return_release_suppresses_the_recapture(self, libei, logger):
        # The landing sits 1 px off the barrier, so the recapture that follows is
        # spurious and must be swallowed.
        session = _session(libei)
        session.captured = True
        session.portal.zones = [(1920, 1080, 0, 0)]
        listener = _active_listener(libei)

        listener.disable_capture(0.5, 0.5, suppress_recapture=True)
        listener._process_commands(session, logger)

        assert session.ignore_next_activation is True

    def test_reject_release_does_not_suppress(self, libei, logger):
        # The ping-pong: a reject armed the token, the next legitimate crossing
        # got swallowed, its release triggered another capture, and round again
        # every few milliseconds - so the pointer never reached the border.
        session = _session(libei)
        session.captured = True
        session.portal.zones = [(1920, 1080, 0, 0)]
        listener = _active_listener(libei)

        listener.disable_capture()
        listener._process_commands(session, logger)

        assert session.ignore_next_activation is False
        session.portal.release.assert_called_once()

    def test_reject_then_crossing_is_not_swallowed(self, libei, logger):
        """A reject must leave the next real activation alone."""
        session = _session(libei)
        session.captured = True
        session.portal.zones = [(1920, 1080, 0, 0)]
        session.portal.activation_id = 5
        session.portal.barrier_id = 1
        session.portal.cursor_position = (1920.0, 400.0)
        listener = _active_listener(libei)
        crossings = []
        listener._on_barrier = lambda edge, cx, cy: crossings.append((edge, cx, cy))

        # Reject on an unbound portion, then a genuine activation.
        listener.disable_capture()
        listener._process_commands(session, logger)
        listener._handle_start_emulating(session, logger)

        assert crossings == [("right", 1920.0, 400.0)]


class TestActivationIdConsumption:
    def test_ignored_activation_is_still_consumed(self, libei, logger):
        """Ignoring an event must not leave the activation id behind.

        Returning before ``poll_activated`` left ``last_activation_id`` stale, so
        the next resolution replayed the skipped activation's coordinates - seen
        in the field as a cursor position frozen across dozens of capture/release
        cycles while the user was really moving, with every routing decision made
        against that stale point.
        """
        session = _session(libei)
        session.ignore_next_activation = True
        session.portal.activation_id = 42
        session.portal.barrier_id = 1
        session.portal.cursor_position = (1920.0, 100.0)
        listener = _active_listener(libei)

        listener._handle_start_emulating(session, logger)

        assert session.last_activation_id == 42
        assert session.ignore_next_activation is False

    def test_next_activation_uses_fresh_coordinates(self, libei, logger):
        session = _session(libei)
        session.ignore_next_activation = True
        session.portal.activation_id = 42
        session.portal.barrier_id = 1
        session.portal.cursor_position = (1920.0, 100.0)
        listener = _active_listener(libei)
        seen = []
        listener._on_barrier = lambda edge, cx, cy: seen.append((cx, cy))

        listener._handle_start_emulating(session, logger)  # swallowed

        # The user has moved on; a new activation arrives.
        session.portal.activation_id = 43
        session.portal.cursor_position = (1920.0, 800.0)
        listener._handle_start_emulating(session, logger)

        assert seen == [(1920.0, 800.0)], "the stale point must not be replayed"

    def test_activation_id_is_published_for_callbacks(self, libei, logger):
        session = _session(libei)
        session.portal.activation_id = 7
        session.portal.barrier_id = 1
        session.portal.cursor_position = (1920.0, 200.0)
        listener = _active_listener(libei)
        listener._on_barrier = lambda *a: None

        listener._handle_start_emulating(session, logger)

        assert listener.current_activation_id == 7


class TestPermissionFailure:
    def test_unauthorised_setup_stops_the_fast_retries(
        self, libei, logger, monkeypatch
    ):
        """A denied dialog can't be fixed by retrying a second later."""
        attempts = []

        def _create(log, keep_waiting=None, portal=None):
            attempts.append(1)
            raise libei._PortalNotAuthorised("access denied")

        monkeypatch.setattr(libei._CaptureSession, "create", _create)
        listener = libei.MouseListener()
        listener._is_running = True

        assert listener._create_session_once(logger) is None
        assert len(attempts) == 1, "must not burn the retry budget on a refusal"
        assert listener._unauthorised is not None

    def test_permission_hints_are_recognised(self, libei):
        assert libei._is_permission_failure("create_session: Access denied")
        assert libei._is_permission_failure("request cancelled by user")
        assert not libei._is_permission_failure("zones request: timed out")

    def test_unauthorised_waits_the_long_delay(self, libei, logger, monkeypatch):
        monkeypatch.setattr(
            libei.MouseListener,
            "_create_session_once",
            lambda self, log: None,
        )
        listener = libei.MouseListener()
        listener._active_edges = {"right"}
        listener._unauthorised = "access denied"

        assert listener._open_session(logger) is libei._NO_SESSION
        # Backing off to the ceiling, not re-asking every second.
        assert listener._reconnect_at > 0
        assert listener._reconnect_pending is True


class TestRepeatedRefusal:
    """Every attempt is another permission dialog; eventually, stop asking."""

    def _refused_listener(self, libei, logger, monkeypatch):
        attempts = []

        def _create(log, keep_waiting=None, portal=None):
            attempts.append(1)
            raise libei._PortalNotAuthorised("access denied")

        monkeypatch.setattr(libei._CaptureSession, "create", _create)
        listener = libei.MouseListener()
        listener._is_running = True
        listener._active_edges = {"right"}
        for _ in range(libei.MouseListener._MAX_NEEDS_USER_ATTEMPTS):
            listener._reconnect_at = 0.0
            listener._maybe_reconnect(logger)
        return listener, attempts

    def test_stops_asking_after_the_cap(self, libei, logger, monkeypatch):
        listener, attempts = self._refused_listener(libei, logger, monkeypatch)
        cap = libei.MouseListener._MAX_NEEDS_USER_ATTEMPTS

        assert listener._capture_blocked is True
        assert len(attempts) == cap

        # Any number of further ticks must not reach the portal again.
        listener._reconnect_at = 0.0
        for _ in range(5):
            assert listener._maybe_reconnect(logger) is libei._NO_SESSION
        assert len(attempts) == cap

    def test_standing_down_arms_no_deadline(self, libei, logger, monkeypatch):
        listener, _ = self._refused_listener(libei, logger, monkeypatch)

        assert listener._reconnect_pending is False

    def test_blocked_still_counts_as_alive(self, libei, logger, monkeypatch):
        """Refused, not broken.

        ``Server._enable_mouse_stream`` restarts the listener whenever
        ``is_alive()`` is False, which would re-open the dialog on a loop and
        undo the stand-down entirely.
        """
        listener, _ = self._refused_listener(libei, logger, monkeypatch)
        listener._thread = MagicMock(is_alive=lambda: True)

        assert listener.is_alive() is True

    def test_a_new_edge_asks_once_more(self, libei, logger, monkeypatch):
        listener, attempts = self._refused_listener(libei, logger, monkeypatch)
        cap = libei.MouseListener._MAX_NEEDS_USER_ATTEMPTS

        # Connecting a client is an explicit request for capture, so it is
        # worth one more dialog.
        listener._cmd_queue.put(
            {"type": "update_clients", "clients": {"right": True, "left": True}}
        )
        listener._idle_wait(logger)

        assert len(attempts) == cap + 1

    def test_the_same_edges_do_not_ask_again(self, libei, logger, monkeypatch):
        listener, attempts = self._refused_listener(libei, logger, monkeypatch)
        cap = libei.MouseListener._MAX_NEEDS_USER_ATTEMPTS

        # A repeated update for an unchanged layout is not a user asking for
        # anything - it is the bus being chatty.
        listener._cmd_queue.put({"type": "update_clients", "clients": {"right": True}})
        listener._idle_wait(logger)

        assert len(attempts) == cap


class TestSetupTimeout:
    """An unanswered dialog must not hold the capture thread for two minutes."""

    def test_timeout_is_passed_when_the_build_takes_it(self, libei):
        class _Portal:
            def __init__(self):
                self.calls = []

            def setup(self, edges, **kwargs):
                self.calls.append((edges, kwargs))
                return ([], 0, [])

        _Portal.setup.__text_signature__ = "($self, edges=None, timeout=120.0)"
        portal = _Portal()

        libei._CaptureSession._setup(portal)

        edges, kwargs = portal.calls[0]
        assert edges == list(libei._ALL_EDGES)
        assert kwargs == {"timeout": libei._SETUP_TIMEOUT}

    def test_an_older_build_still_gets_called(self, libei):
        class _Old:
            def __init__(self):
                self.calls = []

            def setup(self, edges, **kwargs):
                if kwargs:
                    raise TypeError("setup() takes no keyword arguments")
                self.calls.append(edges)
                return ([], 0, [])

        portal = _Old()

        libei._CaptureSession._setup(portal)

        assert portal.calls == [list(libei._ALL_EDGES)]

    def test_backend_info_survives_a_missing_extension(self, libei):
        # pyinputcapture is not installed off Linux; the startup line must still
        # be emittable.
        info = libei._capture_backend_info()

        assert "setup_timeout" in info and "module" in info


class TestUnansweredDialog:
    """A dialog nobody answered is not a transient fault.

    It used to fall through to the 1 s backoff, so an ignored dialog was
    re-requested every couple of seconds forever - and because each attempt
    built a *new* portal object, several ``CreateSession`` requests ended up
    outstanding at once, which is how GNOME stops showing the dialog at all.
    """

    _TIMEOUT = "portal setup timed out after 45s (permission dialog unanswered?)"

    def test_the_setup_timeout_is_recognised(self, libei):
        assert libei._is_setup_timeout(self._TIMEOUT)
        # A zones/barriers timeout happens *after* the dialog was answered and
        # is genuinely transient - it must keep the ordinary backoff.
        assert not libei._is_setup_timeout("zones request: timed out")
        assert not libei._is_permission_failure(self._TIMEOUT)

    def test_create_raises_and_keeps_the_portal(self, libei, logger, monkeypatch):
        pyinputcapture = types.ModuleType("pyinputcapture")
        pyinputcapture.InputCapturePortal = MagicMock()
        monkeypatch.setitem(sys.modules, "pyinputcapture", pyinputcapture)
        portal = MagicMock()

        def _boom(_portal):
            raise RuntimeError(self._TIMEOUT)

        monkeypatch.setattr(libei._CaptureSession, "_setup", staticmethod(_boom))

        with pytest.raises(libei._PortalDialogUnanswered) as caught:
            libei._CaptureSession.create(logger, portal=portal)

        assert caught.value.portal is portal
        # Closing it would drop a request that may still be live, with the
        # dialog on screen - and the next attempt could then overlap it.
        portal.close.assert_not_called()

    def test_the_next_attempt_reuses_the_same_portal(self, libei, logger, monkeypatch):
        kept = MagicMock()
        seen = []

        def _create(log, keep_waiting=None, portal=None):
            seen.append(portal)
            raise libei._PortalDialogUnanswered(self._TIMEOUT, portal=kept)

        monkeypatch.setattr(libei._CaptureSession, "create", _create)
        listener = libei.MouseListener()
        listener._is_running = True

        listener._create_session_once(logger)
        listener._create_session_once(logger)

        assert seen == [None, kept], "a fresh portal per attempt lets two overlap"

    def test_it_counts_towards_the_stand_down(self, libei, logger, monkeypatch):
        monkeypatch.setattr(
            libei._CaptureSession,
            "create",
            lambda log, keep_waiting=None, portal=None: (_ for _ in ()).throw(
                libei._PortalDialogUnanswered(self._TIMEOUT, portal=MagicMock())
            ),
        )
        listener = libei.MouseListener()
        listener._is_running = True
        listener._active_edges = {"right"}

        for _ in range(libei.MouseListener._MAX_NEEDS_USER_ATTEMPTS):
            listener._reconnect_at = 0.0
            listener._maybe_reconnect(logger)

        assert listener._capture_blocked is True
        assert listener.capture_blocked is True

    def test_still_winding_down_is_not_an_attempt(self, libei, logger, monkeypatch):
        # The extension refused before asking the portal anything, so no dialog
        # was raised - counting it would spend the budget on our own throttling.
        pending = "a previous setup is still winding down (the portal has not answered)"
        monkeypatch.setattr(
            libei._CaptureSession,
            "create",
            lambda log, keep_waiting=None, portal=None: (_ for _ in ()).throw(
                libei._PortalDialogUnanswered(pending, portal=MagicMock())
            ),
        )
        listener = libei.MouseListener()
        listener._is_running = True

        for _ in range(5):
            listener._create_session_once(logger)

        assert listener._needs_user_attempts == 0
        assert listener._capture_blocked is False

    def test_the_retry_waits_the_dialog_delay(self, libei, logger, monkeypatch):
        monkeypatch.setattr(
            libei.MouseListener, "_create_session_once", lambda self, log: None
        )
        listener = libei.MouseListener()
        listener._active_edges = {"right"}
        listener._unauthorised = "no answer to the dialog"

        before = time.monotonic()
        listener._open_session(logger)

        # Not the backoff's opening delay: a request still on screen must not be
        # joined by a second one seconds later.
        assert listener._reconnect_at - before >= listener._DIALOG_RETRY_DELAY * 0.9


class TestRequestCapture:
    def test_it_clears_the_stand_down_and_asks_once(self, libei, logger, monkeypatch):
        attempts = []

        def _create(log, keep_waiting=None, portal=None):
            attempts.append(1)
            raise libei._PortalNotAuthorised("access denied")

        monkeypatch.setattr(libei._CaptureSession, "create", _create)
        listener = libei.MouseListener()
        listener._is_running = True
        listener._active_edges = {"right"}
        for _ in range(libei.MouseListener._MAX_NEEDS_USER_ATTEMPTS):
            listener._reconnect_at = 0.0
            listener._maybe_reconnect(logger)
        cap = len(attempts)

        # Reconnecting the same client onto the same edge is an explicit request
        # for capture, but leaves the edge set unchanged - so update_clients
        # alone would not forgive the refusal.
        listener.request_capture()
        listener._idle_wait(logger)

        assert len(attempts) == cap + 1
        listener._capture_blocked = False  # asked and refused again; state is fresh

    def test_it_does_not_ask_without_a_client(self, libei, logger, monkeypatch):
        attempts = []
        monkeypatch.setattr(
            libei._CaptureSession,
            "create",
            lambda log, keep_waiting=None, portal=None: attempts.append(1),
        )
        listener = libei.MouseListener()
        listener._is_running = True
        listener._active_edges = set()

        listener.request_capture()
        listener._idle_wait(logger)

        assert attempts == [], "no client, no dialog"
        assert listener._capture_blocked is False


class TestStartGuard:
    def test_start_refuses_while_the_previous_thread_lives(self, libei):
        # stop()'s join times out precisely when the thread is wedged in
        # portal.setup() on an unanswered dialog: _is_running is already False
        # and is_alive() reports dead, so the service layer restarts us. Starting
        # anyway opens a second portal session while the first still holds an
        # unresolved CreateSession - two concurrent requests against GNOME is a
        # reliable way to get one that never answers.
        listener = libei.MouseListener()
        listener._logger = MagicMock()
        listener._PREVIOUS_THREAD_WAIT = 0.05
        stale = MagicMock(is_alive=MagicMock(return_value=True))
        listener._thread = stale

        listener.start()

        assert listener._is_running is False
        assert listener._thread is stale, "no second capture thread"
        assert listener._logger.warning.called, "the deferral must be reported"

    def test_a_refused_start_is_deferred_not_dropped(self, libei):
        # Only logging the refusal left capture dead for the rest of the
        # process's life: nothing ever asked again once the wedged thread went.
        import threading

        listener = libei.MouseListener()
        listener._logger = MagicMock()
        listener._PREVIOUS_THREAD_WAIT = 2.0
        listener._thread_main = MagicMock()
        gate = threading.Event()
        wedged = threading.Thread(target=gate.wait, daemon=True)
        wedged.start()
        listener._thread = wedged

        listener.start()
        assert listener._is_running is False, (
            "not while the old thread holds the portal"
        )

        gate.set()
        wedged.join(timeout=2)
        try:
            for _ in range(100):
                if listener._is_running:
                    break
                time.sleep(0.02)
            assert listener._is_running is True, "the deferred start never fired"
            assert listener._thread is not wedged
        finally:
            listener.stop()

    def test_a_stop_cancels_a_deferred_start(self, libei):
        # Otherwise the waiter resurrects capture after an explicit stop - and
        # raises a permission dialog for it.
        import threading

        listener = libei.MouseListener()
        listener._logger = MagicMock()
        listener._PREVIOUS_THREAD_WAIT = 2.0
        listener._thread_main = MagicMock()
        gate = threading.Event()
        wedged = threading.Thread(target=gate.wait, daemon=True)
        wedged.start()
        listener._thread = wedged

        listener.start()
        listener.stop()
        gate.set()
        wedged.join(timeout=2)

        time.sleep(0.2)
        assert listener._is_running is False
        assert listener._thread is wedged, "no capture thread after a stop"

    def test_start_proceeds_once_the_previous_thread_is_dead(self, libei):
        listener = libei.MouseListener()
        dead = MagicMock(is_alive=MagicMock(return_value=False))
        listener._thread = dead
        listener._thread_main = MagicMock()

        listener.start()
        try:
            assert listener._is_running is True
            assert listener._thread is not dead
        finally:
            listener.stop()


class TestCapturedStderr:
    def test_captures_what_the_block_wrote_to_fd_2(self, libei):
        # The previous version pointed fd 2 at /dev/tty, which from a terminal
        # launch is a freeze mechanism: SIGTTOU stops the whole process, and a
        # full tty buffer blocks write(2) while logging holds its handler lock.
        captured: list = []

        with libei._captured_stderr(captured):
            os.write(2, b"portal said: not authorised\n")

        assert captured == ["portal said: not authorised"]

    def test_populates_before_an_exception_propagates(self, libei):
        captured: list = []

        with pytest.raises(RuntimeError):
            with libei._captured_stderr(captured):
                os.write(2, b"setup failed\n")
                raise RuntimeError("boom")

        assert captured == ["setup failed"]

    def test_nothing_written_leaves_the_holder_empty(self, libei):
        captured: list = []

        with libei._captured_stderr(captured):
            pass

        assert captured == []


class TestUnauthorisedReset:
    def test_a_later_unrelated_failure_clears_the_refusal(
        self, libei, logger, monkeypatch
    ):
        # Once set, _unauthorised was only cleared on success - so every later
        # failure of any kind reported itself as "not authorised" and waited out
        # the 30 s ceiling.
        monkeypatch.setattr(libei._CaptureSession, "create", lambda *a, **kw: None)
        listener = libei.MouseListener()
        listener._is_running = True
        listener._unauthorised = "access denied"

        assert listener._create_session_once(logger) is None
        assert listener._unauthorised is None

    def test_an_unrelated_failure_does_not_count_towards_the_cap(
        self, libei, logger, monkeypatch
    ):
        # The stand-down is for refusals only: a compositor that is merely
        # unavailable must keep being retried on the backoff.
        monkeypatch.setattr(libei._CaptureSession, "create", lambda *a, **kw: None)
        listener = libei.MouseListener()
        listener._is_running = True
        listener._needs_user_attempts = 2

        listener._create_session_once(logger)

        assert listener._needs_user_attempts == 0
        assert listener._capture_blocked is False

    def test_one_attempt_per_cycle(self, libei, logger, monkeypatch):
        # Three CreateSession requests a second apart is the worst cadence for a
        # request that needs a human to answer a dialog; the backoff owns it now.
        attempts = []
        monkeypatch.setattr(
            libei._CaptureSession,
            "create",
            lambda log, keep_waiting=None, portal=None: attempts.append(1),
        )
        listener = libei.MouseListener()
        listener._is_running = True

        listener._create_session_once(logger)

        assert len(attempts) == 1


class TestTeardown:
    def test_releases_and_closes_a_live_session(self, libei, logger):
        session = _session(libei)
        session.captured = True

        session.teardown()

        session.portal.release.assert_called_once()
        session.portal.close.assert_called_once()
        assert session.captured is False

    def test_dead_session_makes_no_portal_calls(self, libei, logger):
        # Every portal call blocks on a D-Bus reply *holding the GIL*. Once the
        # compositor dropped the session that reply can't come, so calling
        # release/close anyway freezes the whole daemon, not just this thread.
        session = _session(libei)
        session.captured = True
        session.dead = True

        session.teardown()

        session.portal.release.assert_not_called()
        session.portal.close.assert_not_called()


class TestNoBarrierCalls:
    """Barriers are armed once by ``setup()`` and never touched again.

    ``SetPointerBarriers`` is the only way to change them on a live session,
    and GNOME 46 refuses it on an enabled session while being unable to
    re-enable a disabled one - so the call deletes the working barrier set and
    installs nothing. Not calling it is the whole design; these tests are what
    stops it coming back.
    """

    def test_setup_arms_every_edge(self, libei):
        class _Portal:
            def __init__(self):
                self.calls = []

            def setup(self, edges, **kwargs):
                self.calls.append(edges)
                return ([], 0, [])

        portal = _Portal()

        libei._CaptureSession._setup(portal)

        assert sorted(portal.calls[0]) == ["bottom", "left", "right", "top"]

    def test_the_session_has_no_way_to_re_arm(self, libei):
        for name in ("apply_edges", "_arm", "_rearm_cycle", "_set_capture_enabled"):
            assert not hasattr(libei._CaptureSession, name), name

    def test_an_edge_update_makes_no_portal_call(self, libei, logger):
        # A connect/disconnect, a layout edit and a monitor hotplug all come
        # through here. None of them may reach the compositor.
        portal = MagicMock()
        session = _session(libei, portal)
        listener = _active_listener(libei)
        listener._cmd_queue.put(
            {"type": "update_clients", "clients": {"left": True, "right": False}}
        )

        assert listener._process_commands(session, logger) == "continue"

        assert listener._active_edges == {"left"}
        assert portal.method_calls == []

    def test_losing_every_client_does_not_disable_capture(self, libei, logger):
        # ``disable()`` is a one-way trip on GNOME 46. With no client the
        # activation is simply released in Python.
        portal = MagicMock()
        session = _session(libei, portal)
        listener = _active_listener(libei)
        listener._cmd_queue.put({"type": "update_clients", "clients": {}})

        listener._process_commands(session, logger)

        assert listener._active_edges == set()
        assert listener._clients_active is False
        assert portal.method_calls == []


class TestActivationDiagnostics:
    def test_an_unknown_barrier_id_is_logged_before_the_release(self, libei, logger):
        # This branch used to release with no output at all, which is how a
        # barrier set replaced behind our back looked like silence.
        session = _session(libei)
        session.barrier_map = {1: "right"}
        session.portal.activation_id = 3
        session.portal.barrier_id = 99  # not in the map
        session.portal.cursor_position = (1920.0, 400.0)
        listener = _active_listener(libei)

        listener._handle_start_emulating(session, logger)

        rejected = [c for c in logger.debug.call_args_list if "REJECTED" in str(c)]
        assert rejected, "the rejection must be visible"
        assert "bid=99" in str(rejected)
        assert "barrier_map=[1]" in str(rejected)
        session.portal.release.assert_called_once()


class TestReleasePosition:
    def test_denormalises_over_the_union_of_zones(self, libei):
        # Normalised over the server's whole virtual desktop, so zones[0] alone
        # would land the cursor on the wrong monitor.
        portal = MagicMock()
        portal.zones = [(1920, 1080, 0, 0), (1280, 1024, 1920, 0)]

        x, y = libei._CaptureSession.compute_release_pos({"x": 0.5, "y": 0.5}, portal)

        assert x == pytest.approx(1600.0)
        assert y == pytest.approx(540.0)

    def test_clamps_inside_the_union_by_the_margin(self, libei):
        portal = MagicMock()
        portal.zones = [(1920, 1080, 0, 0)]
        margin = libei._CaptureSession._RELEASE_EDGE_MARGIN

        x, y = libei._CaptureSession.compute_release_pos({"x": 0.0, "y": 1.0}, portal)

        assert x == pytest.approx(margin)
        assert y == pytest.approx(1080 - margin)

    def test_margin_is_small_enough_to_reach_the_border(self, libei):
        # The inset exists only to avoid landing on a barrier line; anything
        # larger is visible as the cursor refusing to sit on the border.
        assert libei._CaptureSession._RELEASE_EDGE_MARGIN <= 1.0

    def test_no_position_requested(self, libei):
        portal = MagicMock()
        portal.zones = [(1920, 1080, 0, 0)]

        assert libei._CaptureSession.compute_release_pos(
            {"x": -1, "y": -1}, portal
        ) == (
            None,
            None,
        )

    def test_no_zones(self, libei):
        portal = MagicMock()
        portal.zones = []

        assert libei._CaptureSession.compute_release_pos(
            {"x": 0.5, "y": 0.5}, portal
        ) == (
            None,
            None,
        )


class TestReconnectSchedule:
    def test_disconnect_arms_a_retry_while_edges_remain(self, libei, logger):
        listener = libei.MouseListener()
        listener._active_edges = {"right"}

        listener._schedule_reconnect(logger, "EIS disconnected")

        assert listener._reconnect_pending is True
        assert listener._reconnect_at > 0
        logger.warning.assert_called_once()

    def test_retry_delay_grows_and_is_capped(self, libei, logger):
        listener = libei.MouseListener()
        listener._active_edges = {"right"}

        delays = []
        for _ in range(12):
            listener._schedule_reconnect(logger, "EIS disconnected")
            delays.append(listener._backoff.get_next_delay())

        assert delays[1] > delays[0], "a permanently broken portal must back off"
        assert max(delays) <= listener._SESSION_RETRY_MAX_DELAY * 1.2

    def test_no_edges_stands_down_instead_of_retrying(self, libei, logger):
        listener = libei.MouseListener()
        listener._active_edges = set()

        listener._schedule_reconnect(logger, "EIS disconnected")

        assert listener._reconnect_pending is False

    def test_retry_waits_for_the_deadline(self, libei, logger, monkeypatch):
        listener = libei.MouseListener()
        listener._active_edges = {"right"}
        listener._reconnect_at = float("inf")
        called = MagicMock()
        listener._open_session = called

        assert listener._maybe_reconnect(logger) is libei._NO_SESSION
        called.assert_not_called()

    def test_retry_fires_once_the_deadline_passes(self, libei, logger):
        listener = libei.MouseListener()
        listener._active_edges = {"right"}
        listener._reconnect_at = 0.0
        sentinel = object()
        listener._open_session = MagicMock(return_value=sentinel)

        assert listener._maybe_reconnect(logger) is sentinel

    def test_retry_keeps_trying_after_a_failed_attempt(self, libei, logger):
        # The dead-end this replaces: three quick attempts and then nothing
        # ever again, because only a *new* update_clients built a session.
        listener = libei.MouseListener()
        listener._active_edges = {"right"}
        listener._create_session_once = MagicMock(return_value=None)

        assert listener._open_session(logger) is libei._NO_SESSION
        assert listener._reconnect_pending is True
        assert listener._has_session is False

    def test_a_new_client_cancels_the_backoff_wait(self, libei, logger):
        listener = libei.MouseListener()
        listener._active_edges = {"right"}
        listener._schedule_reconnect(logger, "EIS disconnected")
        listener._create_session_once = MagicMock(return_value=MagicMock())

        listener._idle_wait = libei.MouseListener._idle_wait.__get__(listener)
        listener._cmd_queue.put({"type": "update_clients", "clients": {"left": True}})
        listener._idle_wait(logger)

        assert listener._reconnect_at == 0.0
        assert listener._active_edges == {"left"}


class TestHealthReporting:
    def test_idle_with_no_edges_counts_as_alive(self, libei):
        listener = libei.MouseListener()
        listener._is_running = True
        listener._thread = MagicMock(is_alive=MagicMock(return_value=True))
        listener._active_edges = set()

        assert listener.is_alive() is True

    def test_dead_session_with_edges_bound_is_not_alive(self, libei):
        # Reporting this as alive is what stopped the service layer from ever
        # restarting a listener whose portal session had gone.
        listener = libei.MouseListener()
        listener._is_running = True
        listener._thread = MagicMock(is_alive=MagicMock(return_value=True))
        listener._active_edges = {"right"}
        listener._has_session = False
        listener._reconnect_pending = False

        assert listener.is_alive() is False

    def test_pending_reconnect_still_counts_as_alive(self, libei):
        listener = libei.MouseListener()
        listener._is_running = True
        listener._thread = MagicMock(is_alive=MagicMock(return_value=True))
        listener._active_edges = {"right"}
        listener._has_session = False
        listener._reconnect_pending = True

        assert listener.is_alive() is True

    def test_state_callback_reports_the_reason(self, libei, logger):
        seen = []
        listener = libei.MouseListener(on_state=lambda ok, why: seen.append((ok, why)))
        listener._active_edges = {"right"}

        listener._schedule_reconnect(logger, "EIS disconnected")

        assert seen == [(False, "EIS disconnected")]

    def test_state_callback_failure_is_contained(self, libei, logger):
        listener = libei.MouseListener(on_state=MagicMock(side_effect=RuntimeError))
        listener._active_edges = {"right"}

        listener._schedule_reconnect(logger, "EIS disconnected")


class TestStopResponsiveness:
    def test_interruptible_sleep_aborts_when_stopping(self, libei):
        listener = libei.MouseListener()
        listener._is_running = False

        assert listener._interruptible_sleep(30.0) is False

    def test_interruptible_sleep_completes_when_running(self, libei):
        listener = libei.MouseListener()
        listener._is_running = True

        assert listener._interruptible_sleep(0.0) is True

    def test_seat_wait_aborts_on_stop(self, libei, logger):
        # A stop during the 10 s seat wait used to be ignored for long enough
        # to blow through the join timeout and outlive the service.
        receiver = MagicMock()
        receiver.events = []
        libei._wait_for_seat(receiver, logger, keep_waiting=lambda: False)

        receiver.dispatch.assert_not_called()


class TestUpdateClientsPayload:
    def test_accepts_the_wrapped_edges_shape(self, libei):
        listener = libei.MouseListener()

        listener.update_clients({"edges": {"left": True}})

        cmd = listener._cmd_queue.get_nowait()
        assert cmd["clients"] == {"left": True}

    def test_accepts_a_bare_edge_mapping(self, libei):
        listener = libei.MouseListener()

        listener.update_clients({"left": True})

        cmd = listener._cmd_queue.get_nowait()
        assert cmd["clients"] == {"left": True}

    def test_an_empty_payload_means_no_edges(self, libei):
        listener = libei.MouseListener()

        listener.update_clients({"edges": {}})

        cmd = listener._cmd_queue.get_nowait()
        assert cmd["clients"] == {}
