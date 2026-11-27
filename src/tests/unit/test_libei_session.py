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

import sys
import threading
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

    ei = types.ModuleType("snegg.ei")
    ei.Sender = MagicMock()
    ei.Receiver = MagicMock()
    ei.EventType = _EventType
    ei.DeviceCapability = _DeviceCapability

    c_libei = types.ModuleType("snegg.c.libei")
    c_libei.libei = MagicMock()

    evdev = types.ModuleType("evdev")
    evdev.ecodes = types.SimpleNamespace(BTN_LEFT=272, BTN_RIGHT=273, BTN_MIDDLE=274)

    for name, mod in (
        ("snegg", types.ModuleType("snegg")),
        ("snegg.ei", ei),
        ("snegg.c", types.ModuleType("snegg.c")),
        ("snegg.c.libei", c_libei),
        ("evdev", evdev),
    ):
        monkeypatch.setitem(sys.modules, name, mod)


@pytest.fixture
def libei(monkeypatch, tmp_path):
    _install_snegg_stubs(monkeypatch)
    from input.mouse.backend import _portal

    # Never the user's real token file, and a fresh registration per test.
    monkeypatch.setattr(_portal, "_store", _portal.TokenStore(str(tmp_path / "t.json")))
    monkeypatch.setattr(_portal, "_register_tried", False)
    monkeypatch.setattr(_portal, "_registered", None)
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

        def _create(log, keep_waiting=None):
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
            lambda self, log, trigger="test": None,
        )
        listener = libei.MouseListener()
        listener._is_running = True
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

        def _create(log, keep_waiting=None):
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

    def test_the_dialog_wait_is_bounded(self, libei):
        class _Portal:
            def __init__(self):
                self.calls = []

            def setup(self, edges, **kwargs):
                self.calls.append((edges, kwargs))
                return ([], 0, [])

        portal = _Portal()

        libei._CaptureSession._setup(portal)

        edges, kwargs = portal.calls[0]
        assert edges == list(libei._ALL_EDGES)
        assert kwargs == {"timeout": libei._SETUP_TIMEOUT}


class TestCaptureRegistersTheAppId:
    def test_before_the_capture_portal(self, libei, logger, monkeypatch):
        # A server-first daemon would otherwise fix the app id with its
        # InputCapture call, and the client's grants would land elsewhere.
        calls = []
        mod = types.ModuleType("pyinputcapture")
        mod.register_host_app = MagicMock(
            side_effect=lambda a: calls.append("register")
        )
        mod.InputCapturePortal = MagicMock(
            side_effect=lambda: calls.append("portal") or MagicMock()
        )
        monkeypatch.setitem(sys.modules, "pyinputcapture", mod)
        monkeypatch.setattr(
            libei._CaptureSession,
            "_setup",
            staticmethod(lambda _p: (_ for _ in ()).throw(RuntimeError("boom"))),
        )

        libei._CaptureSession.create(logger)

        assert calls == ["register", "portal"]


class TestUnansweredDialog:
    """A dialog nobody answered is not a transient fault.

    It belongs on the human-paced schedule, not the backoff. The portal object
    of the failed attempt is not kept: the D-Bus connection carrying the request
    is process-global in the extension and outlives every object.
    """

    _TIMEOUT = "portal setup timed out after 45s (permission dialog unanswered?)"

    def test_the_setup_timeout_is_recognised(self, libei):
        assert libei._is_setup_timeout(self._TIMEOUT)
        # A zones/barriers timeout happens *after* the dialog was answered and
        # is genuinely transient - it must keep the ordinary backoff.
        assert not libei._is_setup_timeout("zones request: timed out")
        assert not libei._is_permission_failure(self._TIMEOUT)

    def test_create_raises_and_closes_the_portal(self, libei, logger, monkeypatch):
        portal = MagicMock()
        pyinputcapture = types.ModuleType("pyinputcapture")
        pyinputcapture.InputCapturePortal = MagicMock(return_value=portal)
        monkeypatch.setitem(sys.modules, "pyinputcapture", pyinputcapture)

        def _boom(_portal):
            raise RuntimeError(self._TIMEOUT)

        monkeypatch.setattr(libei._CaptureSession, "_setup", staticmethod(_boom))

        with pytest.raises(libei._PortalDialogUnanswered):
            libei._CaptureSession.create(logger)

        # Nothing is carried forward: keeping the object could only wedge the
        # next attempt on this one's task, and the abandoned request does not
        # stop the next dialog (measured).
        portal.close.assert_called_once()

    def test_the_extensions_own_reason_reaches_the_log(
        self, libei, logger, monkeypatch
    ):
        # The capture thread has fd 2 pointed at /dev/null for its whole life, so
        # ``last_error`` is the only place a late task error is recoverable from.
        portal = MagicMock()
        portal.last_error = "create_session: Access denied"
        pyinputcapture = types.ModuleType("pyinputcapture")
        pyinputcapture.InputCapturePortal = MagicMock(return_value=portal)
        monkeypatch.setitem(sys.modules, "pyinputcapture", pyinputcapture)
        monkeypatch.setattr(
            libei._CaptureSession,
            "_setup",
            staticmethod(
                lambda _p: (_ for _ in ()).throw(RuntimeError("setup failed"))
            ),
        )

        with pytest.raises(libei._PortalNotAuthorised) as caught:
            libei._CaptureSession.create(logger)

        # And it is what classifies the failure: "setup failed" alone says nothing.
        assert "Access denied" in str(caught.value)

    def test_it_counts_towards_the_stand_down(self, libei, logger, monkeypatch):
        monkeypatch.setattr(
            libei._CaptureSession,
            "create",
            lambda log, keep_waiting=None: (_ for _ in ()).throw(
                libei._PortalDialogUnanswered(self._TIMEOUT)
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

    def test_the_retry_waits_the_dialog_delay(self, libei, logger, monkeypatch):
        monkeypatch.setattr(
            libei.MouseListener,
            "_create_session_once",
            lambda self, log, trigger="test": None,
        )
        listener = libei.MouseListener()
        listener._is_running = True
        listener._active_edges = {"right"}
        listener._unauthorised = "no answer to the dialog"

        before = time.monotonic()
        listener._open_session(logger)

        # Not the backoff's opening delay: a request still on screen must not be
        # joined by a second one seconds later.
        assert listener._reconnect_at - before >= listener._DIALOG_RETRY_DELAY * 0.9


class TestRequestedUpdate:
    """A client connecting is the user asking for capture."""

    def test_it_clears_the_stand_down_and_asks_once(self, libei, logger, monkeypatch):
        attempts = []

        def _create(log, keep_waiting=None):
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

        # Same client, same edge: only the request flag says "ask again".
        listener.update_clients({"edges": {"right": True}}, requested=True)
        listener._idle_wait(logger)

        assert len(attempts) == cap + 1
        assert listener._capture_blocked is False  # refused once more, not capped

    def test_it_does_not_ask_without_a_client(self, libei, logger, monkeypatch):
        attempts = []
        monkeypatch.setattr(
            libei._CaptureSession,
            "create",
            lambda log, keep_waiting=None: attempts.append(1),
        )
        listener = libei.MouseListener()
        listener._is_running = True

        listener.update_clients({"edges": {}}, requested=True)
        listener._idle_wait(logger)

        assert attempts == [], "no client, no dialog"

    def test_a_connect_is_one_dialog_not_two(self, libei, logger, monkeypatch):
        # update_clients followed by request_capture used to open two sessions,
        # and the second reset the refusal count, so the cap never fired.
        attempts = []

        def _create(log, keep_waiting=None):
            attempts.append(1)
            raise libei._PortalNotAuthorised("access denied")

        monkeypatch.setattr(libei._CaptureSession, "create", _create)
        listener = libei.MouseListener()
        listener._is_running = True

        listener.update_clients({"edges": {"right": True}}, requested=True)
        while not listener._cmd_queue.empty():
            listener._idle_wait(logger)

        assert len(attempts) == 1
        assert listener._needs_user_attempts == 1


class TestUnrequestedUpdate:
    """Layout chatter must not reset the schedule or raise a dialog."""

    def _waiting_listener(self, libei, monkeypatch):
        attempts = []
        monkeypatch.setattr(
            libei._CaptureSession,
            "create",
            lambda log, keep_waiting=None: attempts.append(1),
        )
        listener = libei.MouseListener()
        listener._is_running = True
        listener._active_edges = {"left", "right"}
        listener._reconnect_at = time.monotonic() + 30.0
        listener._reconnect_pending = True
        return listener, attempts

    @pytest.mark.parametrize(
        "edges", [{"left": True, "right": True}, {"right": True}], ids=["same", "fewer"]
    )
    def test_same_or_fewer_edges_keep_the_schedule(
        self, libei, logger, monkeypatch, edges
    ):
        listener, attempts = self._waiting_listener(libei, monkeypatch)
        deadline = listener._reconnect_at

        listener.update_clients({"edges": edges})
        listener._idle_wait(logger)

        assert attempts == []
        assert listener._reconnect_at == deadline

    def test_a_new_edge_asks_now(self, libei, logger, monkeypatch):
        listener, attempts = self._waiting_listener(libei, monkeypatch)

        listener.update_clients({"edges": {"left": True, "top": True}})
        listener._idle_wait(logger)

        assert attempts == [1]


class TestLateApproval:
    """A dialog approved after stop() is kept for the next listener."""

    @pytest.fixture(autouse=True)
    def _no_parked(self, libei):
        libei._take_parked_session()
        yield
        libei._take_parked_session()

    def _session(self, libei, enabled):
        session = MagicMock()
        session.enabled = enabled
        return session

    @staticmethod
    def _stopped_during_setup(libei, session):
        """A listener whose idle wait returns ``session`` after stop() landed."""
        listener = libei.MouseListener()
        listener._is_running = True

        def _idle_wait(logger):
            listener._is_running = False
            return session

        listener._idle_wait = _idle_wait
        return listener

    def test_create_does_not_enable_once_stopped(self, libei, logger, monkeypatch):
        portal = MagicMock()
        portal.setup.return_value = ([(1920, 1080, 0, 0)], 7, [(1, "right")])
        monkeypatch.setitem(
            sys.modules,
            "pyinputcapture",
            types.SimpleNamespace(InputCapturePortal=lambda: portal),
        )
        monkeypatch.setattr(libei, "_ei_from_fd", MagicMock())
        monkeypatch.setattr(libei._select, "poll", MagicMock())

        session = libei._CaptureSession.create(logger, keep_waiting=lambda: False)

        assert session.enabled is False
        portal.enable.assert_not_called()

    def test_the_stopped_thread_parks_it_and_the_next_listener_adopts_it(
        self, libei, logger, monkeypatch
    ):
        late = self._session(libei, enabled=False)
        old = self._stopped_during_setup(libei, late)
        old._thread_main()

        late.teardown.assert_not_called()
        create = MagicMock()
        monkeypatch.setattr(libei._CaptureSession, "create", create)
        new = libei.MouseListener()
        new._is_running = True

        assert new._create_session_once(logger) is late
        late.activate.assert_called_once()
        create.assert_not_called()

    def test_an_enabled_session_is_never_parked(self, libei, logger):
        # Enabled barriers with no listener would capture the pointer for good.
        live = self._session(libei, enabled=True)
        old = self._stopped_during_setup(libei, live)
        old._thread_main()

        live.teardown.assert_called_once()
        assert libei._take_parked_session() is None


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
        try:
            assert listener._is_running is False
            assert listener._thread is stale, "no second capture thread"
            assert listener._logger.warning.called, "the deferral must be reported"
        finally:
            listener.stop()  # ends the waiter, which never gives up on its own

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


class TestDeferredStartWaits:
    def test_a_long_wait_is_reported_not_given_up(self, libei):
        # Giving up after _PREVIOUS_THREAD_WAIT left capture dead for good.
        states = []
        listener = libei.MouseListener(on_state=lambda ok, why: states.append(ok))
        listener._logger = MagicMock()
        listener._PREVIOUS_THREAD_WAIT = 0.05
        listener._thread_main = MagicMock()
        gate = threading.Event()
        wedged = threading.Thread(target=gate.wait, daemon=True)
        wedged.start()
        listener._thread = wedged

        listener.start()
        try:
            time.sleep(0.8)
            assert states == [False], "the delay must be reported once"
            assert listener._is_running is False

            gate.set()
            for _ in range(100):
                if listener._is_running:
                    break
                time.sleep(0.02)
            assert listener._is_running is True
        finally:
            gate.set()
            listener.stop()


class TestStderrRedirect:
    def test_the_last_holder_restores_whatever_the_exit_order(self, libei):
        import os

        before = os.fstat(2)
        first = libei._suppress_libei_stderr()
        second = libei._suppress_libei_stderr()

        libei._restore_stderr(first)  # not LIFO
        devnull = os.stat(os.devnull)
        assert os.fstat(2).st_rdev == devnull.st_rdev, "still held by the second"

        libei._restore_stderr(second)
        after = os.fstat(2)
        assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)


class TestInFlightGuard:
    """Two permission dialogs must never be on screen at once.

    The guard is module-level because the case it exists for is a listener
    rebuilt after ``Server.cleanup()``, which cannot see the previous capture
    thread still inside ``setup()``.
    """

    def _blocking_create(self, libei, monkeypatch):
        """A ``create`` that parks until released, counting its calls."""
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def _create(log, keep_waiting=None):
            calls.append(1)
            entered.set()
            release.wait(timeout=5)
            return MagicMock()

        monkeypatch.setattr(libei._CaptureSession, "create", _create)
        return entered, release, calls

    def test_two_queued_triggers_produce_one_dialog(self, libei, logger, monkeypatch):
        entered, release, calls = self._blocking_create(libei, monkeypatch)
        listener = libei.MouseListener()
        listener._is_running = True

        first = threading.Thread(
            target=listener._create_session_once, args=(logger,), daemon=True
        )
        first.start()
        assert entered.wait(timeout=5)

        assert listener._create_session_once(logger) is libei._IN_FLIGHT, (
            "no second dialog"
        )
        assert len(calls) == 1

        release.set()
        first.join(timeout=5)

    def test_a_new_listener_cannot_ask_over_the_old_one(
        self, libei, logger, monkeypatch
    ):
        # The restarted-service case: Server.cleanup() dropped the listener while
        # its thread was still blocked in setup(), so the guard has to be shared
        # by objects that have never heard of each other.
        entered, release, calls = self._blocking_create(libei, monkeypatch)
        old = libei.MouseListener()
        old._is_running = True
        new = libei.MouseListener()
        new._is_running = True

        wedged = threading.Thread(
            target=old._create_session_once, args=(logger,), daemon=True
        )
        wedged.start()
        assert entered.wait(timeout=5)

        assert new._create_session_once(logger) is libei._IN_FLIGHT
        assert len(calls) == 1

        release.set()
        wedged.join(timeout=5)

    def test_a_skipped_attempt_costs_nothing(self, libei, logger, monkeypatch):
        # Nothing was asked of the user, so it must not burn an attempt from the
        # stand-down budget or invent a refusal.
        entered, release, _ = self._blocking_create(libei, monkeypatch)
        listener = libei.MouseListener()
        listener._is_running = True
        blocker = libei.MouseListener()
        blocker._is_running = True

        wedged = threading.Thread(
            target=blocker._create_session_once, args=(logger,), daemon=True
        )
        wedged.start()
        assert entered.wait(timeout=5)

        listener._create_session_once(logger)

        assert listener._needs_user_attempts == 0
        assert listener._capture_blocked is False

        release.set()
        wedged.join(timeout=5)

    def test_the_lock_is_released_when_create_raises(self, libei, logger, monkeypatch):
        attempts = []

        def _create(log, keep_waiting=None):
            attempts.append(1)
            raise libei._PortalNotAuthorised("access denied")

        monkeypatch.setattr(libei._CaptureSession, "create", _create)
        listener = libei.MouseListener()
        listener._is_running = True

        listener._create_session_once(logger)
        listener._create_session_once(logger)

        assert len(attempts) == 2, "a failed attempt must not wedge the guard"


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

        # Neither counted nor forgiven: a flaky compositor between two refusals
        # must not buy a fourth dialog.
        assert listener._needs_user_attempts == 2
        assert listener._capture_blocked is False

    def test_one_attempt_per_cycle(self, libei, logger, monkeypatch):
        # Three CreateSession requests a second apart is the worst cadence for a
        # request that needs a human to answer a dialog; the backoff owns it now.
        attempts = []
        monkeypatch.setattr(
            libei._CaptureSession,
            "create",
            lambda log, keep_waiting=None: attempts.append(1),
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

        rejected = [
            c.kwargs
            for c in logger.debug.call_args_list
            if c.args == ("Activation not routed",)
        ]
        assert rejected, "the rejection must be visible"
        assert rejected[0]["barrier_id"] == 99
        assert rejected[0]["barrier_ids"] == [1]
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
        listener._is_running = True
        listener._active_edges = {"right"}
        listener._create_session_once = MagicMock(return_value=None)

        assert listener._open_session(logger) is libei._NO_SESSION
        assert listener._reconnect_pending is True
        assert listener._has_session is False

    def test_a_new_client_cancels_the_backoff_wait(self, libei, logger):
        listener = libei.MouseListener()
        listener._is_running = True
        listener._active_edges = {"right"}
        listener._schedule_reconnect(logger, "EIS disconnected")
        listener._create_session_once = MagicMock(return_value=MagicMock())

        listener._idle_wait = libei.MouseListener._idle_wait.__get__(listener)
        listener._cmd_queue.put({"type": "update_clients", "clients": {"left": True}})
        listener._idle_wait(logger)

        assert listener._reconnect_at == 0.0
        assert listener._active_edges == {"left"}


class TestIdleIsNotASession:
    def test_no_client_reports_idle_not_a_live_session(self, libei, logger):
        # "healthy" with no session read as one being up before any client.
        states = []
        listener = libei.MouseListener(
            on_state=lambda ok, why: states.append((ok, why))
        )
        listener._is_running = True

        listener.update_clients({"edges": {}})
        listener._idle_wait(logger)

        assert states == [(True, libei._IDLE_REASON)]

    def test_a_live_session_reports_no_reason(self, libei, logger):
        states = []
        listener = libei.MouseListener(
            on_state=lambda ok, why: states.append((ok, why))
        )
        listener._is_running = True
        listener._active_edges = {"right"}
        listener._create_session_once = MagicMock(return_value=MagicMock())

        listener._open_session(logger)

        assert states == [(True, None)]


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


class TestUnknownEventTypes:
    """libei >= 1.x emits events snegg's enum lacks (EI_EVENT_SYNC = 91, seen
    on GNOME 50); ``event.event_type`` raises ValueError on them."""

    @staticmethod
    def _unknown_event():
        event = MagicMock()
        type(event).event_type = property(
            lambda self: (_ for _ in ()).throw(
                ValueError("91 is not a valid EventType")
            )
        )
        return event

    def test_event_type_is_none_for_an_unknown_value(self, libei):
        assert libei._event_type(self._unknown_event()) is None

    def test_dispatch_loop_skips_it_and_still_sees_the_disconnect(
        self, libei, monkeypatch
    ):
        disconnect = MagicMock(event_type=libei.EventType.DISCONNECT)
        conn = libei._EiConnection()
        conn._sender = MagicMock()
        conn._sender.events = [self._unknown_event(), disconnect]
        conn._device = MagicMock()
        poller = MagicMock()
        poller.poll.return_value = [(0, 1)]
        monkeypatch.setattr(libei._select, "poll", lambda: poller)

        conn._dispatch_loop()

        # An escaping ValueError would end the thread with _error still None:
        # the connection would look healthy with nobody dispatching it.
        assert conn._device is None
        assert "disconnected" in str(conn._error)


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


class _FakeConn:
    """Stands in for ``_EiConnection``; ``outcome`` decides what connect() does."""

    def __init__(self, outcome=None, gate=None, dialog=False):
        self.outcome = outcome
        self.gate = gate
        self.dialog = dialog
        self._error = None
        self._device = None
        self._closing = False
        self.closed = False

    def connect(self, consent_timeout, on_dialog=None):
        if self.dialog and on_dialog is not None:
            on_dialog()
        if self.gate is not None:
            self.gate.wait(2)
        if self.outcome is not None:
            raise self.outcome
        self._device = MagicMock()

    def close(self):
        self.closed = True

    def abort(self):
        self._closing = True


class TestRemoteDesktopLink:
    """Client RemoteDesktop session: off-thread connect, capped retries, and a
    terminal DENIED that only ``reset()`` (a user action) clears."""

    @staticmethod
    def _link(libei, outcomes, monkeypatch=None):
        """A link whose n-th connection attempt gets ``outcomes[n]``."""
        made = []

        def factory():
            outcome = outcomes[min(len(made), len(outcomes) - 1)]
            conn = outcome if isinstance(outcome, _FakeConn) else _FakeConn(outcome)
            made.append(conn)
            return conn

        link = libei._RemoteDesktopLink(connection_factory=factory)
        link._retry_at = 0.0
        return link, made

    def test_connection_never_blocks_the_caller(self, libei):
        gate = threading.Event()
        link, made = self._link(libei, [_FakeConn(gate=gate)])

        started = time.monotonic()
        assert link.connection() is None
        assert time.monotonic() - started < 0.1
        assert link.state is libei._LinkState.CONNECTING

        gate.set()
        assert _wait_until(lambda: link.state is libei._LinkState.ACTIVE)
        assert link.connection() is made[0]

    def test_a_refusal_denies_at_once_and_asks_nothing_more(self, libei):
        link, made = self._link(libei, [libei._ConsentRefused("no")])
        heard = []
        link.add_failure_listener(heard.append)

        link.connection()
        assert _wait_until(lambda: link.state is libei._LinkState.DENIED)
        for _ in range(5):
            assert link.connection() is None

        assert len(made) == 1
        assert heard == ["no"]
        assert made[0].closed

    def test_the_technical_cause_goes_to_the_log_not_the_reason(self, libei):
        refused = libei._ConsentRefused("Remote input permission was denied")
        refused.__cause__ = RuntimeError("portal said 2")
        link, _ = self._link(libei, [refused])
        link._logger = logger = MagicMock()
        link.connection()
        assert _wait_until(lambda: link.state is libei._LinkState.DENIED)

        assert link.denied_reason == "Remote input permission was denied"
        logged = logger.warning.call_args.kwargs
        assert logged["detail"] == "portal said 2"

    def test_an_unanswered_dialog_denies(self, libei):
        link, _ = self._link(libei, [libei._ConsentTimeout("not answered")])
        link.connection()
        assert _wait_until(lambda: link.state is libei._LinkState.DENIED)
        assert link.denied_reason == "not answered"

    def test_transient_failures_are_capped(self, libei):
        link, made = self._link(libei, [RuntimeError("flaky")])
        heard = []
        link.add_failure_listener(heard.append)

        for _ in range(libei._RemoteDesktopLink.MAX_TRANSIENT_FAILURES * 3):
            link._retry_at = 0.0
            link.connection()
            _wait_until(lambda: link.state is not libei._LinkState.CONNECTING)
            if link.state is libei._LinkState.DENIED:
                break

        assert link.state is libei._LinkState.DENIED
        assert len(made) == libei._RemoteDesktopLink.MAX_TRANSIENT_FAILURES
        assert len(heard) == 1

    def test_a_dialog_is_announced_then_its_grant(self, libei):
        gate = threading.Event()
        link, _ = self._link(libei, [_FakeConn(gate=gate, dialog=True)])
        heard = []
        link.add_consent_listener(heard.append)

        link.connection()
        assert _wait_until(lambda: heard == ["pending"])
        assert link.awaiting_consent

        gate.set()
        assert _wait_until(lambda: heard == ["pending", "granted"])
        assert not link.awaiting_consent

    def test_a_silent_restore_announces_nothing(self, libei):
        link, _ = self._link(libei, [_FakeConn()])
        heard = []
        link.add_consent_listener(heard.append)
        link.connection()
        assert _wait_until(lambda: link.state is libei._LinkState.ACTIVE)
        assert heard == []

    def test_a_refused_dialog_is_no_longer_awaited(self, libei):
        link, _ = self._link(
            libei, [_FakeConn(libei._ConsentRefused("no"), dialog=True)]
        )
        heard = []
        link.add_consent_listener(heard.append)
        link.connection()
        assert _wait_until(lambda: link.state is libei._LinkState.DENIED)
        assert heard == ["pending"]
        assert not link.awaiting_consent

    def test_a_closed_link_drops_a_late_dialog(self, libei):
        gate = threading.Event()
        conn = _FakeConn(gate=gate)
        link, _ = self._link(libei, [conn])
        heard = []
        link.add_consent_listener(heard.append)
        link.connection()
        assert _wait_until(lambda: link._pending is conn)
        link.close()
        conn.dialog = True
        link._notify_consent(link._generation - 1, "pending")
        gate.set()
        assert heard == []
        assert not link.awaiting_consent

    def test_a_transient_failure_waits_out_the_backoff(self, libei):
        link, made = self._link(libei, [RuntimeError("flaky")])
        link.connection()
        assert _wait_until(lambda: link.state is libei._LinkState.IDLE)

        link.connection()  # retry not due yet
        assert len(made) == 1
        assert link._retry_at > time.monotonic()

    def test_reset_after_a_denial_asks_again(self, libei):
        link, made = self._link(libei, [libei._ConsentRefused("no"), _FakeConn()])
        link.connection()
        assert _wait_until(lambda: link.state is libei._LinkState.DENIED)

        link.reset()
        link.connection()
        assert _wait_until(lambda: link.state is libei._LinkState.ACTIVE)
        assert len(made) == 2

    def test_a_session_dropped_by_the_compositor_is_reopened(self, libei):
        link, made = self._link(libei, [_FakeConn(), _FakeConn()])
        link.connection()
        assert _wait_until(lambda: link.state is libei._LinkState.ACTIVE)

        made[0]._error = RuntimeError("libei: disconnected by compositor")
        assert link.connection() is None
        assert _wait_until(lambda: link.state is libei._LinkState.ACTIVE)
        assert link.connection() is made[1]
        assert made[0].closed

    def test_close_abandons_an_attempt_in_flight(self, libei):
        gate = threading.Event()
        link, made = self._link(libei, [_FakeConn(gate=gate)])
        link.connection()
        assert _wait_until(lambda: link._pending is not None)

        link.close()
        assert made[0]._closing
        gate.set()

        assert _wait_until(lambda: made[0].closed)
        assert link.connection() is not made[0]


class TestControllerWithoutSession:
    def test_input_before_the_grant_is_dropped_quietly(self, libei):
        # Used to raise _PortalBackoff on every event, logged at mouse rate.
        controller = libei.MouseController.__new__(libei.MouseController)
        controller._x = controller._y = 0
        controller._link = MagicMock()
        controller._link.connection.return_value = None

        controller.move(5, 5)
        controller.position = (10, 10)
        controller.scroll(0, 1)

        assert (controller._x, controller._y) == (10, 10)


def _rd_module(monkeypatch, portal, calls=None):
    """A ``pyinputcapture`` whose RemoteDesktopPortal() returns ``portal``."""
    calls = calls if calls is not None else []
    mod = types.ModuleType("pyinputcapture")
    mod.register_host_app = MagicMock(side_effect=lambda app: calls.append("register"))

    def _make():
        calls.append("portal")
        return portal

    mod.RemoteDesktopPortal = MagicMock(side_effect=_make)
    monkeypatch.setitem(sys.modules, "pyinputcapture", mod)
    return mod


class TestConsentWait:
    """``_EiConnection.connect`` up to the EIS fd: the consent dialog."""

    def _conn(self, libei, monkeypatch, outcome, calls=None):
        portal = MagicMock()
        if isinstance(outcome, BaseException):
            portal.setup.side_effect = outcome
        elif callable(outcome):
            portal.setup.side_effect = outcome
        else:
            portal.setup.return_value = outcome
        _rd_module(monkeypatch, portal, calls)
        poller = MagicMock()
        poller.poll.return_value = [(0, 1)]
        monkeypatch.setattr(libei._select, "poll", lambda: poller)
        conn = libei._EiConnection()
        conn.test_portal = portal
        return conn

    @staticmethod
    def _store():
        from input.mouse.backend import _portal

        return _portal.token_store()

    @pytest.mark.parametrize("message", ["start: Cancelled", ""])
    def test_a_refusal_reads_cleanly(self, libei, monkeypatch, message):
        # The reason is shown in the GUI; the portal's text goes to the log.
        conn = self._conn(libei, monkeypatch, PermissionError(message))
        with pytest.raises(libei._ConsentRefused) as raised:
            conn.connect(consent_timeout=5)
        assert str(raised.value) == "Remote input permission was denied"

    def test_a_refusal_forgets_the_token(self, libei, monkeypatch):
        self._store().save("remote_desktop", "old", "com.federicoizzi.Perpetua")
        conn = self._conn(libei, monkeypatch, PermissionError("start: Cancelled"))
        with pytest.raises(libei._ConsentRefused):
            conn.connect(consent_timeout=5)
        assert self._store().load("remote_desktop", None) is None

    def test_a_transient_failure_keeps_the_token(self, libei, monkeypatch):
        self._store().save("remote_desktop", "old", "com.federicoizzi.Perpetua")
        conn = self._conn(libei, monkeypatch, RuntimeError("connect_to_eis: boom"))
        with pytest.raises(RuntimeError) as raised:
            conn.connect(consent_timeout=5)
        assert not isinstance(raised.value, libei._ConsentRefused)
        assert self._store().load("remote_desktop", None) == "old"

    def test_an_unanswered_dialog_times_out(self, libei, monkeypatch):
        conn = self._conn(libei, monkeypatch, TimeoutError("not answered"))
        with pytest.raises(libei._ConsentTimeout):
            conn.connect(consent_timeout=5)

    def test_the_saved_token_is_presented_and_the_new_one_kept(
        self, libei, monkeypatch
    ):
        self._store().save("remote_desktop", "t1", "com.federicoizzi.Perpetua")
        conn = self._conn(libei, monkeypatch, (42, "t2"))
        monkeypatch.setattr(libei, "_ei_from_fd", MagicMock(side_effect=StopTest))
        with pytest.raises(StopTest):
            conn.connect(consent_timeout=7)

        kwargs = conn.test_portal.setup.call_args.kwargs
        assert kwargs["restore_token"] == "t1"
        assert kwargs["persist"] is True
        assert kwargs["timeout"] == 7
        assert self._store().load("remote_desktop", None) == "t2"

    def test_without_a_token_the_dialog_is_announced_first(self, libei, monkeypatch):
        order = []

        def setup(**_):
            order.append("setup")
            raise StopTest

        conn = self._conn(libei, monkeypatch, setup)
        with pytest.raises(StopTest):
            conn.connect(consent_timeout=5, on_dialog=lambda: order.append("dialog"))
        assert order == ["dialog", "setup"]

    def test_with_a_token_no_dialog_is_announced(self, libei, monkeypatch):
        self._store().save("remote_desktop", "t1", "com.federicoizzi.Perpetua")
        conn = self._conn(libei, monkeypatch, StopTest())
        announced = []
        with pytest.raises(StopTest):
            conn.connect(consent_timeout=5, on_dialog=lambda: announced.append(1))
        assert announced == []

    def test_the_app_id_is_registered_before_the_first_portal(self, libei, monkeypatch):
        calls = []
        conn = self._conn(libei, monkeypatch, (42, None), calls)
        monkeypatch.setattr(libei, "_ei_from_fd", MagicMock(side_effect=StopTest))
        with pytest.raises(StopTest):
            conn.connect(consent_timeout=5)
        assert calls[:2] == ["register", "portal"]
        sys.modules["pyinputcapture"].register_host_app.assert_called_once_with(
            "com.federicoizzi.Perpetua"
        )

    def test_abort_closes_the_portal_and_drops_a_late_grant(self, libei, monkeypatch):
        conn = None

        def granted_while_aborting(**_):
            conn.abort()
            return 42, "t2"

        conn = self._conn(libei, monkeypatch, granted_while_aborting)
        with pytest.raises(RuntimeError, match="aborted"):
            conn.connect(consent_timeout=5)
        conn.test_portal.close.assert_called()
        conn.close()
        assert conn._portal is None


class StopTest(Exception):
    """Ends ``connect()`` right after the EIS fd was obtained."""


class TestDeviceAcquisition:
    """The compositor offers relative and absolute pointers as two devices."""

    _conn = TestConsentWait._conn

    @staticmethod
    def _device(libei, *caps):
        dev = MagicMock()
        dev.capabilities = set(caps)
        return dev

    @staticmethod
    def _event(etype, **kw):
        ev = MagicMock(**kw)
        ev.event_type = etype
        return ev

    def _connect_with(self, libei, monkeypatch, batches):
        conn = self._conn(libei, monkeypatch, (42, None))
        sender = MagicMock()
        sender.events = []
        feed = iter(batches)

        def dispatch():
            sender.events = next(feed, [])

        sender.dispatch.side_effect = dispatch
        monkeypatch.setattr(libei, "_ei_from_fd", MagicMock(return_value=sender))
        monkeypatch.setattr(libei._EiConnection, "_start_dispatch", MagicMock())
        conn.connect(consent_timeout=5)
        return conn

    def test_both_pointers_are_taken_and_the_regions_read(self, libei, monkeypatch):
        ET, DC = libei.EventType, libei.DeviceCapability
        rel = self._device(libei, DC.POINTER, DC.BUTTON, DC.SCROLL)
        absolute = self._device(libei, DC.POINTER_ABSOLUTE, DC.BUTTON, DC.SCROLL)
        region = object()
        libei.libei.device_get_region.side_effect = lambda c, i: (
            region if i == 0 else None
        )
        libei.libei.region_get_x.return_value = 0
        libei.libei.region_get_y.return_value = 0
        libei.libei.region_get_width.return_value = 960
        libei.libei.region_get_height.return_value = 576
        libei.libei.region_get_physical_scale.return_value = 1.333

        conn = self._connect_with(
            libei,
            monkeypatch,
            [
                [self._event(ET.SEAT_ADDED, seat=MagicMock())],
                # The relative pointer is ready before the absolute one is
                # even announced: stopping there would lose the warp device.
                [self._event(ET.DEVICE_ADDED, device=rel)],
                [self._event(ET.DEVICE_RESUMED, device=rel)],
                [self._event(ET.DEVICE_ADDED, device=absolute)],
                [self._event(ET.DEVICE_RESUMED, device=absolute)],
            ],
        )

        assert conn._device is rel, "buttons and relative motion"
        assert conn._abs_device is absolute
        assert conn.regions == [(0, 0, 960, 576, 1.333)]

    def test_a_single_device_is_enough_once_the_list_is_quiet(self, libei, monkeypatch):
        ET, DC = libei.EventType, libei.DeviceCapability
        absolute = self._device(libei, DC.POINTER_ABSOLUTE, DC.BUTTON)
        libei.libei.device_get_region.side_effect = lambda c, i: None

        conn = self._connect_with(
            libei,
            monkeypatch,
            [
                [self._event(ET.SEAT_ADDED, seat=MagicMock())],
                [self._event(ET.DEVICE_ADDED, device=absolute)],
                [self._event(ET.DEVICE_RESUMED, device=absolute)],
            ],
        )

        assert conn._device is absolute and conn._abs_device is absolute


class TestWarpUsesTheAbsoluteDevice:
    def test_position_goes_to_the_absolute_device(self, libei):
        conn = MagicMock(paused=False)
        link = MagicMock()
        link.connection.return_value = conn
        controller = object.__new__(libei.MouseController)
        controller._x = controller._y = 0
        controller._link = link

        controller.position = (640, 400)

        conn.abs_device.pointer_motion_absolute.assert_called_once_with(640.0, 400.0)
        conn.device.pointer_motion.assert_not_called()
