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
        # unbound part of the edge just keeps stalling as it did before.
        portal = MagicMock()
        portal.set_barriers.side_effect = [
            TypeError("unexpected keyword"),
            [(1, "right")],
        ]
        session = _session(libei, portal, armed=())

        session.apply_edges({"right"}, [("right", 1920, 0, 1920, 540)], logger)

        assert portal.set_barriers.call_args_list[-1].args == (["right"],)
        assert session.armed_edges == {"right"}
        assert session.armed_segments == ()

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
        listener._create_session_with_retry = MagicMock(return_value=None)

        assert listener._open_session(logger) is libei._NO_SESSION
        assert listener._reconnect_pending is True
        assert listener._has_session is False

    def test_a_new_client_cancels_the_backoff_wait(self, libei, logger):
        listener = libei.MouseListener()
        listener._active_edges = {"right"}
        listener._schedule_reconnect(logger, "EIS disconnected")
        listener._create_session_with_retry = MagicMock(return_value=MagicMock())

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
