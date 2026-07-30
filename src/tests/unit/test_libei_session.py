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
the barrier arming, and the teardown guard that keeps a dead session from
making another blocking portal call.
"""

import os
import sys
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


def _portal_with_signature(signature, result=None, error=None):
    """A portal whose ``set_barriers`` advertises an explicit text signature.

    A ``MagicMock`` can't carry a dunder like ``__text_signature__``, and the
    capability probe reads exactly that - so the signature-sensitive tests need a
    real object.
    """

    class _SetBarriers:
        __text_signature__ = signature

        def __init__(self):
            self.calls = []

        def __call__(self, *args, **kwargs):
            self.calls.append(args or kwargs)
            if error is not None:
                raise error
            return result or []

    class _Portal:
        def __init__(self):
            self.set_barriers = _SetBarriers()
            self.zones = [(1920, 1080, 0, 0)]

        @property
        def calls(self):
            return self.set_barriers.calls

        def enable(self):
            pass

        def disable(self):
            pass

        def release(self, x, y):
            pass

        def close(self):
            pass

    return _Portal()


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

        def _create(edges, log, keep_waiting=None):
            attempts.append(edges)
            raise libei._PortalNotAuthorised("access denied")

        monkeypatch.setattr(libei._CaptureSession, "create", _create)
        listener = libei.MouseListener()
        listener._is_running = True

        assert listener._create_session_once(["right"], logger) is None
        assert len(attempts) == 1, "must not burn the retry budget on a refusal"
        assert listener._unauthorised == "access denied"

    def test_permission_hints_are_recognised(self, libei):
        assert libei._is_permission_failure("create_session: Access denied")
        assert libei._is_permission_failure("request cancelled by user")
        assert not libei._is_permission_failure("zones request: timed out")

    def test_unauthorised_waits_the_long_delay(self, libei, logger, monkeypatch):
        monkeypatch.setattr(
            libei.MouseListener,
            "_create_session_once",
            lambda self, edges, log: None,
        )
        listener = libei.MouseListener()
        listener._active_edges = {"right"}
        listener._unauthorised = "access denied"

        assert listener._open_session(logger) is libei._NO_SESSION
        # Backing off to the ceiling, not re-asking every second.
        assert listener._reconnect_at > 0
        assert listener._reconnect_pending is True


class TestSegmentCapabilityWarning:
    def test_warns_once_when_set_barriers_is_missing(self, libei, logger):
        # Silent degradation is how a whole-edge barrier across an unbound
        # stretch of screen looked like a mystery rather than a missing rebuild.
        portal = MagicMock(spec=["zones", "release", "close", "enable", "disable"])
        session = _session(libei, portal, armed=())

        session.apply_edges({"right"}, [("right", 1920, 0, 1920, 540)], logger)
        session.apply_edges({"left"}, [("left", 0, 0, 0, 540)], logger)

        warnings = [c for c in logger.warning.call_args_list if "maturin" in str(c)]
        assert len(warnings) == 1

    def test_no_warning_when_segments_are_supported(self, libei, logger):
        portal = MagicMock()
        portal.set_barriers.return_value = [(1, "right")]
        session = _session(libei, portal, armed=())

        session.apply_edges({"right"}, [("right", 1920, 0, 1920, 540)], logger)

        assert not [c for c in logger.warning.call_args_list if "maturin" in str(c)]


class TestSegmentCapabilityProbe:
    """The capability is decided by inspection, never by catching TypeError.

    PyO3 raises ``TypeError`` for a failed argument extraction exactly as it does
    for an unknown keyword, so inferring "this build can't do segments" from the
    exception reported a malformed segment - a float where an ``i32`` is wanted -
    as a missing rebuild. That is the warning the field report was seeing *with*
    the new build installed.
    """

    def test_absent_set_barriers_is_the_only_hard_no(self, libei):
        portal = MagicMock(spec=["zones", "release", "close", "enable", "disable"])

        assert libei._segments_supported(portal) is False

    def test_signature_without_segments_reports_false(self, libei):
        portal = _portal_with_signature("($self, edges)")

        assert libei._segments_supported(portal) is False

    def test_signature_with_segments_reports_true(self, libei):
        portal = _portal_with_signature("($self, edges=None, segments=None)")

        assert libei._segments_supported(portal) is True

    def test_unreadable_signature_is_not_evidence_of_absence(self, libei):
        # No ``__text_signature__`` at all: assume supported, so a genuine
        # argument error can surface instead of being mislabelled.
        portal = MagicMock()

        assert libei._segments_supported(portal) is True

    def test_a_float_coordinate_is_reported_as_a_bad_segment(self, libei, logger):
        bad = ("right", 1920, 0.5, 1920, 540)
        portal = _portal_with_signature(
            "($self, edges=None, segments=None)",
            error=TypeError("argument 'segments': failed to extract field"),
        )
        session = _session(libei, portal, armed=())

        session.apply_edges({"right"}, [bad], logger)

        errors = str(logger.error.call_args_list)
        assert "0.5" in errors, "the offending segment must be named"
        assert not [c for c in logger.warning.call_args_list if "maturin" in str(c)]
        assert session.segments_supported is True

    def test_malformed_segments_picks_out_the_offenders(self, libei):
        good = ("right", 1920, 0, 1920, 540)
        floaty = ("right", 1920.0, 0, 1920, 540)
        short = ("right", 1920, 0, 1920)

        assert libei._malformed_segments([good]) == []
        assert libei._malformed_segments([good, floaty, short]) == [floaty, short]

    def test_backend_info_survives_a_missing_extension(self, libei):
        # pyinputcapture is not installed off Linux; the startup line must still
        # be emittable.
        info = libei._capture_backend_info()

        assert "segments" in info and "module" in info


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
        stale = MagicMock(is_alive=MagicMock(return_value=True))
        listener._thread = stale

        listener.start()

        assert listener._is_running is False
        assert listener._thread is stale, "no second capture thread"
        assert listener._logger.warning.called, "the refusal must be reported"

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

        assert listener._create_session_once(["right"], logger) is None
        assert listener._unauthorised is None

    def test_one_attempt_per_cycle(self, libei, logger, monkeypatch):
        # Three CreateSession requests a second apart is the worst cadence for a
        # request that needs a human to answer a dialog; the backoff owns it now.
        attempts = []
        monkeypatch.setattr(
            libei._CaptureSession,
            "create",
            lambda edges, log, keep_waiting=None: attempts.append(edges),
        )
        listener = libei.MouseListener()
        listener._is_running = True

        listener._create_session_once(["right"], logger)

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


class TestBarrierArming:
    def test_arms_only_the_requested_segments(self, libei, logger):
        portal = MagicMock()
        portal.set_barriers.return_value = [(1, "right")]
        session = _session(libei, portal, armed=())

        segments = [("right", 1920, 0, 1920, 540)]
        session.apply_edges({"right"}, segments, logger)

        portal.set_barriers.assert_called_once_with(segments=segments)
        assert session.armed_edges == {"right"}
        assert session.barrier_map == {1: "right"}

    def test_no_clients_disables_capture_entirely(self, libei, logger):
        # Nothing to cross to anywhere: the whole border must be reachable.
        portal = MagicMock()
        session = _session(libei, portal)

        session.apply_edges(set(), [], logger)

        portal.disable.assert_called_once()
        assert session.armed_edges == set()
        assert session.enabled is False

    def test_re_enables_when_a_client_comes_back(self, libei, logger):
        portal = MagicMock()
        portal.set_barriers.return_value = [(1, "left")]
        session = _session(libei, portal)

        session.apply_edges(set(), [], logger)
        session.apply_edges({"left"}, [("left", 0, 0, 0, 1079)], logger)

        portal.enable.assert_called_once()
        assert session.enabled is True
        assert session.armed_edges == {"left"}

    def test_unchanged_request_does_not_re_issue(self, libei, logger):
        portal = MagicMock()
        portal.set_barriers.return_value = [(1, "right")]
        session = _session(libei, portal, armed=())
        segments = [("right", 1920, 0, 1920, 1079)]

        session.apply_edges({"right"}, segments, logger)
        session.apply_edges({"right"}, segments, logger)

        assert portal.set_barriers.call_count == 1

    def test_falls_back_to_whole_edges_without_segment_support(self, libei, logger):
        # An older installed pyinputcapture: still better than nothing, the
        # unbound part of the edge just keeps stalling as it did before. The
        # capability is read off the signature, so the segments call is never
        # even attempted.
        portal = _portal_with_signature("($self, edges)", [(1, "right")])
        session = _session(libei, portal, armed=())

        session.apply_edges({"right"}, [("right", 1920, 0, 1920, 540)], logger)

        assert portal.calls == [(["right"],)]
        assert session.armed_edges == {"right"}
        assert session.armed_segments == ()
        assert [c for c in logger.warning.call_args_list if "maturin" in str(c)]

    def test_set_barriers_failure_leaves_state_untouched(self, libei, logger):
        portal = MagicMock()
        portal.set_barriers.side_effect = RuntimeError("portal said no")
        session = _session(libei, portal, armed=("top",))

        session.apply_edges({"right"}, [("right", 1920, 0, 1920, 540)], logger)

        assert session.armed_edges == {"top"}
        logger.warning.assert_called_once()

    def test_dead_session_is_not_re_armed(self, libei, logger):
        portal = MagicMock()
        session = _session(libei, portal)
        session.dead = True

        session.apply_edges({"left"}, [("left", 0, 0, 0, 100)], logger)

        portal.set_barriers.assert_not_called()
        portal.enable.assert_not_called()


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
        listener._cmd_queue.put(
            {
                "type": "update_clients",
                "clients": {"left": True},
                "segments": [("left", 0, 0, 0, 100)],
            }
        )
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
    def test_accepts_the_edges_plus_segments_shape(self, libei):
        listener = libei.MouseListener()
        segments = [("left", 0, 0, 0, 100)]

        listener.update_clients({"edges": {"left": True}, "segments": segments})

        cmd = listener._cmd_queue.get_nowait()
        assert cmd["clients"] == {"left": True}
        assert cmd["segments"] == segments

    def test_accepts_a_bare_edge_mapping(self, libei):
        listener = libei.MouseListener()

        listener.update_clients({"left": True})

        cmd = listener._cmd_queue.get_nowait()
        assert cmd["clients"] == {"left": True}
        assert cmd["segments"] == []
