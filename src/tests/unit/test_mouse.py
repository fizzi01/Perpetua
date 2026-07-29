"""
Unit tests for mouse module components.
Tests EdgeDetector, ServerMouseListener, ServerMouseController, and ClientMouseController.
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
from tests.unit import _MOCK_PYNPUT

import asyncio
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from event import (
    BusEventType,
    MouseEvent,
    ActiveScreenChangedEvent,
    ClientConnectedEvent,
    ClientDisconnectedEvent,
    ClientActiveEvent,
    ClientLayoutUpdatedEvent,
    CrossScreenCommandEvent,
)

from model.client import ScreenPosition
from network.stream import StreamType

_MOCK_PYNPUT()

from input.mouse._base import (  # noqa: E402
    EdgeDetector,
    ScreenEdge,
    ServerMouseListener,
    ServerMouseController,
    ClientMouseController,
    ButtonMapping,
)
from utils.screen import MonitorLayout  # noqa: E402


def _patch_screen_geometry(w: int, h: int):
    # Patch every Screen accessor the mouse code uses so the listener
    # and controller see a single (w, h) display at origin (0, 0).
    layout = MonitorLayout.from_bboxes([(0, 0, w, h)])
    return [
        patch("input.mouse._base.Screen.get_size", return_value=(w, h)),
        patch(
            "input.mouse._base.Screen.get_virtual_bbox",
            return_value=(0, 0, w, h),
        ),
        patch(
            "input.mouse._base.Screen.get_monitor_layout",
            return_value=layout,
        ),
    ]


from contextlib import ExitStack  # noqa: E402


class _ScreenGeometry:
    def __init__(self, w: int, h: int):
        self._patches = _patch_screen_geometry(w, h)
        self._stack = ExitStack()

    def __enter__(self):
        for p in self._patches:
            self._stack.enter_context(p)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stack.close()


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_stream_handler():
    """Mock StreamHandler for testing."""
    handler = AsyncMock()
    handler.send = AsyncMock()
    handler.register_receive_callback = MagicMock()
    return handler


@pytest.fixture
def mock_mouse_listener():
    """Mock pynput MouseListener."""
    listener = MagicMock()
    listener.start = MagicMock()
    listener.stop = MagicMock()
    listener.is_alive = MagicMock(return_value=False)
    return listener


@pytest.fixture
def mock_mouse_controller():
    """Mock pynput MouseController."""
    controller = MagicMock()
    controller.position = (0, 0)
    controller.move = MagicMock()
    controller.press = MagicMock()
    controller.release = MagicMock()
    controller.scroll = MagicMock()
    return controller


@pytest.fixture
def edge_detector():
    """Provide EdgeDetector instance."""
    return EdgeDetector()


@pytest.fixture
def screen_size():
    """Standard screen size for testing."""
    return (1920, 1080)


# ============================================================================
# EdgeDetector Tests
# ============================================================================


class TestEdgeDetector:
    """Test EdgeDetector functionality."""

    def test_is_at_edge_left_moving_left(self, screen_size):
        """Test detection of left edge when moving left."""
        movement_history = [
            (10, 500),
            (8, 500),
            (6, 500),
            (4, 500),
            (2, 500),
            (0, 500),
        ]
        edge = EdgeDetector.is_at_edge(movement_history, 0, 500, screen_size, False)
        assert edge == ScreenEdge.LEFT

    def test_is_at_edge_right_moving_right(self, screen_size):
        """Test detection of right edge when moving right."""
        movement_history = [
            (1910, 500),
            (1912, 500),
            (1914, 500),
            (1916, 500),
            (1918, 500),
            (1919, 500),
        ]
        edge = EdgeDetector.is_at_edge(movement_history, 1919, 500, screen_size, False)
        assert edge == ScreenEdge.RIGHT

    def test_is_at_edge_top_moving_top(self, screen_size):
        """Test detection of top edge when moving up."""
        movement_history = [
            (500, 10),
            (500, 8),
            (500, 6),
            (500, 4),
            (500, 2),
            (500, 0),
        ]
        edge = EdgeDetector.is_at_edge(movement_history, 500, 0, screen_size, False)
        assert edge == ScreenEdge.TOP

    def test_is_at_edge_bottom_moving_bottom(self, screen_size):
        """Test detection of bottom edge when moving down."""
        movement_history = [
            (500, 1070),
            (500, 1072),
            (500, 1074),
            (500, 1076),
            (500, 1078),
            (500, 1079),
        ]
        edge = EdgeDetector.is_at_edge(movement_history, 500, 1079, screen_size, False)
        assert edge == ScreenEdge.BOTTOM

    def test_is_at_edge_at_left_but_moving_right(self, screen_size):
        """Test no detection when at left edge but moving right."""
        movement_history = [
            (0, 500),
            (2, 500),
            (4, 500),
            (6, 500),
            (8, 500),
            (10, 500),
        ]
        edge = EdgeDetector.is_at_edge(movement_history, 0, 500, screen_size, False)
        assert edge is None

    def test_is_at_edge_moving_left_but_not_at_edge(self, screen_size):
        """Test no detection when moving left but not at edge."""
        movement_history = [
            (110, 500),
            (108, 500),
            (106, 500),
            (104, 500),
            (102, 500),
            (100, 500),
        ]
        edge = EdgeDetector.is_at_edge(movement_history, 100, 500, screen_size, False)
        assert edge is None

    def test_is_at_edge_while_dragging(self, screen_size):
        """Test no detection when dragging."""
        movement_history = [
            (10, 500),
            (8, 500),
            (6, 500),
            (4, 500),
            (2, 500),
            (0, 500),
        ]
        edge = EdgeDetector.is_at_edge(movement_history, 0, 500, screen_size, True)
        assert edge is None

    def test_is_at_edge_erratic_movement(self, screen_size):
        """Test no detection with erratic movement."""
        movement_history = [
            (10, 500),
            (5, 500),
            (15, 500),
            (3, 500),
            (12, 500),
            (0, 500),
        ]
        edge = EdgeDetector.is_at_edge(movement_history, 0, 500, screen_size, False)
        assert edge is None

    def test_detect_edge_calls_callback(self, edge_detector, screen_size):
        """Test that detect_edge calls the appropriate callback."""
        movement_history = [
            (10, 500),
            (8, 500),
            (6, 500),
            (4, 500),
            (2, 500),
            (0, 500),
        ]
        callback_mock = MagicMock()
        callbacks = {ScreenEdge.LEFT: callback_mock}

        edge_detector.detect_edge(
            movement_history, 0, 500, screen_size, False, callbacks
        )

        callback_mock.assert_called_once()

    def test_detect_edge_no_callback_if_no_edge(self, edge_detector, screen_size):
        """Test that no callback is called when not at edge."""
        movement_history = [
            (110, 500),
            (108, 500),
            (106, 500),
            (104, 500),
            (102, 500),
            (100, 500),
        ]
        callback_mock = MagicMock()
        callbacks = {ScreenEdge.LEFT: callback_mock}

        edge_detector.detect_edge(
            movement_history, 100, 500, screen_size, False, callbacks
        )

        callback_mock.assert_not_called()

    def test_clamp_to_screen_within_bounds(self, screen_size):
        """Test coordinates within bounds remain unchanged."""
        x, y = EdgeDetector.clamp_to_screen(100, 200, screen_size)
        assert x == 100
        assert y == 200

    def test_clamp_to_screen_left_edge(self, screen_size):
        """Test clamping coordinates beyond left edge."""
        x, y = EdgeDetector.clamp_to_screen(-5, 500, screen_size)
        assert x == 0
        assert y == 500

    def test_clamp_to_screen_right_edge(self, screen_size):
        """Test clamping coordinates beyond right edge."""
        x, y = EdgeDetector.clamp_to_screen(1925, 500, screen_size)
        assert x == 1919  # screen_size[0] - 1
        assert y == 500

    def test_clamp_to_screen_top_edge(self, screen_size):
        """Test clamping coordinates beyond top edge."""
        x, y = EdgeDetector.clamp_to_screen(500, -10, screen_size)
        assert x == 500
        assert y == 0

    def test_clamp_to_screen_bottom_edge(self, screen_size):
        """Test clamping coordinates beyond bottom edge."""
        x, y = EdgeDetector.clamp_to_screen(500, 1085, screen_size)
        assert x == 500
        assert y == 1079  # screen_size[1] - 1

    def test_clamp_to_screen_multiple_edges(self, screen_size):
        """Test clamping coordinates beyond multiple edges."""
        x, y = EdgeDetector.clamp_to_screen(-10, -20, screen_size)
        assert x == 0
        assert y == 0

        x, y = EdgeDetector.clamp_to_screen(2000, 1100, screen_size)
        assert x == 1919
        assert y == 1079


# ============================================================================
# ServerMouseListener Tests
# ============================================================================


class TestServerMouseListener:
    """Test ServerMouseListener functionality."""

    @pytest.mark.anyio
    async def test_start_creates_and_starts_listener(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_listener,
    ):
        """Test that start() creates and starts a mouse listener."""
        with patch("input.mouse._base.MouseListener", return_value=mock_mouse_listener):
            listener = ServerMouseListener(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
                filtering=False,
            )
            mock_mouse_listener.is_alive.return_value = False

            result = listener.start()

            assert result is True
            mock_mouse_listener.start.assert_called_once()

    @pytest.mark.anyio
    async def test_stop_stops_listener(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_listener,
    ):
        """Test that stop() stops the mouse listener."""
        with patch("input.mouse._base.MouseListener", return_value=mock_mouse_listener):
            listener = ServerMouseListener(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
                filtering=False,
            )
            listener._listener = mock_mouse_listener
            mock_mouse_listener.is_alive.return_value = True

            result = listener.stop()

            assert result is True
            mock_mouse_listener.stop.assert_called_once()

    @pytest.mark.anyio
    async def test_on_client_connected_adds_to_active_screens(
        self,
        event_bus,
        mock_stream_handler,
    ):
        """Test that client connection adds to active screens."""
        listener = ServerMouseListener(
            event_bus,
            mock_stream_handler,
            mock_stream_handler,
            filtering=False,
        )

        event = ClientConnectedEvent(
            client_uid="client1",
            streams=[StreamType.MOUSE, StreamType.KEYBOARD],
        )

        await listener._on_client_connected(event)

        assert "client1" in listener._active_clients
        assert listener._active_clients["client1"] is True

    @pytest.mark.anyio
    async def test_on_client_connected_ignores_without_mouse_stream(
        self,
        event_bus,
        mock_stream_handler,
    ):
        """Test that client without mouse stream is not added."""
        listener = ServerMouseListener(
            event_bus,
            mock_stream_handler,
            mock_stream_handler,
            filtering=False,
        )

        event = ClientConnectedEvent(
            client_uid="client1",
            streams=[StreamType.KEYBOARD],  # No MOUSE stream
        )

        await listener._on_client_connected(event)

        assert "client1" not in listener._active_clients

    @pytest.mark.anyio
    async def test_on_client_disconnected_removes_from_active_screens(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_listener,
    ):
        """Test that client disconnection removes from active screens."""
        with patch("input.mouse._base.MouseListener", return_value=mock_mouse_listener):
            listener = ServerMouseListener(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
                filtering=False,
            )
            listener._active_clients["client1"] = True
            listener._listening = True

            event = ClientDisconnectedEvent(
                client_uid="client1",
                streams=[StreamType.MOUSE],
            )

            await listener._on_client_disconnected(event)

            assert "client1" not in listener._active_clients

    @pytest.mark.anyio
    async def test_on_client_disconnected_stops_listening_when_empty(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_listener,
    ):
        """Test that listener flag is set to false when all clients disconnect."""
        with patch("input.mouse._base.MouseListener", return_value=mock_mouse_listener):
            listener = ServerMouseListener(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
                filtering=False,
            )
            listener._active_clients["client1"] = True
            listener._listening = True

            event = ClientDisconnectedEvent(
                client_uid="client1",
                streams=[StreamType.MOUSE],
            )

            await listener._on_client_disconnected(event)

            assert listener._listening is False

    @pytest.mark.anyio
    async def test_on_active_screen_changed_starts_listening(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_listener,
    ):
        """Test that active screen change starts listening."""
        with patch("input.mouse._base.MouseListener", return_value=mock_mouse_listener):
            listener = ServerMouseListener(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
                filtering=False,
            )
            listener._listening = False

            event = ActiveScreenChangedEvent(
                active_screen="client1",
                source="server",
                position=(0.5, 0.5),
            )

            await listener._on_active_screen_changed(event)

            assert listener._listening is True
            assert len(listener._movement_history) == 0

    @pytest.mark.anyio
    async def test_on_active_screen_changed_stops_listening(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_listener,
    ):
        """Test that active screen change to None stops listening."""
        with patch("input.mouse._base.MouseListener", return_value=mock_mouse_listener):
            listener = ServerMouseListener(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
                filtering=False,
            )
            listener._listening = True

            event = ActiveScreenChangedEvent(
                active_screen=None,
                source="server",
                position=(0.5, 0.5),
            )

            await listener._on_active_screen_changed(event)

            assert listener._listening is False

    @pytest.mark.anyio
    async def test_recross_guard_after_return_blocks_then_unlocks(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_listener,
    ):
        """After a return, re-crossing the same edge is suppressed until the
        cursor moves inward off it - the retained edge-ward history would
        otherwise re-fire on the next tick."""
        with patch("input.mouse._base.MouseListener", return_value=mock_mouse_listener):
            with _ScreenGeometry(1920, 1080):
                listener = ServerMouseListener(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                    filtering=False,
                )
                # Return landed ~6px inside the RIGHT edge (client was off the
                # right). Arms the re-cross lock on the RIGHT edge.
                event = ActiveScreenChangedEvent(
                    active_screen=None,
                    source="server",
                    position=(1913, 500),
                )
                await listener._on_active_screen_changed(event)
                assert listener._recross_locked_edge == ScreenEdge.RIGHT

                # Still near the edge -> a RIGHT crossing is suppressed, while a
                # crossing through a different edge is not (and leaves the lock).
                assert listener._recross_lock_blocks(ScreenEdge.RIGHT, 1913, 500)
                assert not listener._recross_lock_blocks(ScreenEdge.LEFT, 1913, 500)
                assert listener._recross_locked_edge == ScreenEdge.RIGHT

                # Cursor moves inward past the margin -> lock clears, RIGHT
                # crossings are allowed again.
                assert not listener._recross_lock_blocks(ScreenEdge.RIGHT, 1900, 500)
                assert listener._recross_locked_edge is None

    @pytest.mark.anyio
    async def test_on_click_sends_event_when_listening(
        self,
        event_bus,
        mock_stream_handler,
    ):
        """Test that click events are sent when listening."""
        Button = MagicMock()
        Button.left.name = "left"

        with _ScreenGeometry(1920, 1080):
            listener = ServerMouseListener(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
                filtering=False,
            )
            listener._listening = True
            listener.on_click(100, 200, Button.left, True)

        mock_stream_handler.send.assert_called_once()
        args = mock_stream_handler.send.call_args[0]
        # Coordinates are normalized
        assert args[0].x == pytest.approx(100 / 1920)
        assert args[0].y == pytest.approx(200 / 1080)
        assert args[0].button == ButtonMapping.left.value
        assert args[0].is_pressed is True

    @pytest.mark.anyio
    async def test_on_scroll_sends_event_when_listening(
        self,
        event_bus,
        mock_stream_handler,
    ):
        """Test that scroll events are sent when listening."""
        listener = ServerMouseListener(
            event_bus,
            mock_stream_handler,
            mock_stream_handler,
            filtering=False,
        )
        listener._listening = True

        listener.on_scroll(100, 200, 1, -1)

        mock_stream_handler.send.assert_called_once()
        args = mock_stream_handler.send.call_args[0]
        # Scroll events only contain dx and dy
        assert args[0].dx == 1
        assert args[0].dy == -1
        assert args[0].action == MouseEvent.SCROLL_ACTION

    @pytest.mark.anyio
    async def test_on_move_updates_movement_history(
        self,
        event_bus,
        mock_stream_handler,
    ):
        """Test that mouse move updates movement history."""
        listener = ServerMouseListener(
            event_bus,
            mock_stream_handler,
            mock_stream_handler,
            filtering=False,
        )
        listener._listening = False

        # Stub _screen_size_valid to bypass the
        # early-return guard so on_move populates the movement history.
        listener._screen_size_valid = lambda: True  # type: ignore[method-assign]

        listener.on_move(100, 200)

        assert len(listener._movement_history) == 1
        assert listener._movement_history[0] == (100, 200)


# ============================================================================
# ServerMouseController Tests
# ============================================================================


class TestServerMouseController:
    """Test ServerMouseController functionality."""

    @pytest.mark.anyio
    async def test_on_active_screen_changed_positions_cursor(
        self, event_bus, mock_mouse_controller
    ):
        """Test that cursor is positioned when active screen becomes None."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ServerMouseController(event_bus)

            event = ActiveScreenChangedEvent(
                active_screen=None,
                source="client1",
                position=(0.5, 0.3),
            )

            with _ScreenGeometry(1920, 1080):
                await controller._on_active_screen_changed(event)

            mock_mouse_controller.position = (960, 324)

    @pytest.mark.anyio
    async def test_position_cursor_sets_position(
        self, event_bus, mock_mouse_controller
    ):
        """Test that position_cursor sets the cursor position."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with _ScreenGeometry(1920, 1080):
                controller = ServerMouseController(event_bus)

                # Coordinates are normalized (0-1 range)
                controller.position_cursor(0.5, 0.6)

                # Should denormalize to absolute coordinates
                assert mock_mouse_controller.position == (960, 648)

    @pytest.mark.anyio
    async def test_position_cursor_clamps_to_screen_bounds(
        self, event_bus, mock_mouse_controller
    ):
        """Out-of-range normalized values land at the screen edge, not past it."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with _ScreenGeometry(1920, 1080):
                controller = ServerMouseController(event_bus)

                # Normalized 2.0 > 1.0: must clamp to last on-screen pixel
                # (w-1, h-1) rather than land off-screen at (3840, 2160).
                controller.position_cursor(2.0, 2.0)
                assert mock_mouse_controller.position == (1919, 1079)

                # Negative values clamp to (0, 0) as well.
                controller.position_cursor(-0.5, -0.5)
                assert mock_mouse_controller.position == (0, 0)


# ============================================================================
# ClientMouseController Tests
# ============================================================================


class TestClientMouseController:
    """Test ClientMouseController functionality."""

    @pytest.mark.anyio
    async def test_start_creates_worker_task(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test that start() creates a worker task."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )

            await controller.start()

            assert controller._running is True
            assert controller._worker_task is not None
            assert not controller._worker_task.done()

            await controller.stop()

    @pytest.mark.anyio
    async def test_stop_cancels_worker_task(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test that stop() cancels the worker task."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )

            await controller.start()
            await asyncio.sleep(0.1)
            await controller.stop()

            assert controller._running is False

    @pytest.mark.anyio
    async def test_on_client_active_starts_controller(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test that client active event starts controller."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )

            event = ClientActiveEvent(client_uid="server")

            await controller._on_client_active(event)

            assert controller._is_active is True
            assert controller._running is True
            # No explicit landing / entry edge -> nothing locked.
            assert controller._return_locked_edge is None
            assert controller._inward_travel == 0

            await controller.stop()

    @pytest.mark.anyio
    async def test_on_client_active_locks_entry_edge(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """The server-supplied entry edge locks return-to-server on activation."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )

            event = ClientActiveEvent(
                client_uid="server",
                position_x=0.0,
                position_y=0.5,
                entry_edge="left",
            )
            await controller._on_client_active(event)

            assert controller._return_locked_edge == ScreenEdge.LEFT
            assert controller._inward_travel == 0

            await controller.stop()

    @pytest.mark.anyio
    async def test_on_client_inactive_resets_state(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test that client inactive event resets state."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )
            controller._is_active = True
            controller._movement_history.append((100, 200))
            controller._return_locked_edge = ScreenEdge.LEFT
            controller._inward_travel = 5

            event = ClientActiveEvent(client_uid="server")

            await controller._on_client_inactive(event)

            assert controller._is_active is False
            assert len(controller._movement_history) == 0
            assert controller._return_locked_edge is None
            assert controller._inward_travel == 0

    def _make_client(self, event_bus, mock_stream_handler, mock_mouse_controller):
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            return ClientMouseController(
                event_bus, mock_stream_handler, mock_stream_handler
            )

    def test_inward_travel_tracks_applied_displacement(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """``_inward_travel`` follows the cursor, not the delta we were sent.

        A backend reports what the OS really applied: on macOS the deltas go
        through the HID system, so an app holding the pointer keeps the cursor
        still and no travel happens. Crediting the raw delta instead would
        saturate the offset and latch ``_return_armed`` against a cursor that
        never left the edge.
        """
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._active_target_bbox = (0, 0, 1920, 1080)
        c._return_locked_edge = ScreenEdge.LEFT

        with patch.object(c, "_inject_relative", return_value=(0, 0)):
            for _ in range(200):
                c._move_cursor(-1, -1, 40, 0)
        assert c._inward_travel == 0
        assert c._return_armed is False
        # The raw delta is still cached — it is a direction hint for
        # ``_detect_edge_via_delta``, not a displacement.
        assert c._last_move_delta == (40, 0)

        with patch.object(c, "_inject_relative", return_value=(40, 0)):
            for _ in range(3):
                c._move_cursor(-1, -1, 40, 0)
        assert c._inward_travel == 120
        assert c._return_armed is True
        assert c._last_move_delta == (40, 0)

    @pytest.mark.anyio
    async def test_return_to_server_survives_an_immobile_burst(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """After a burst that moved nothing, the entry edge must still return.

        A pointer held by a foreground app used to saturate ``_inward_travel``
        from the raw deltas, closing the return gate for good, so every later
        tick fell through to ``_clamp_cursor_to_monitor`` — dead movement.
        """
        with _ScreenGeometry(1920, 1080):
            c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
            self._activate_left_entry(c)

            # The app holds the pointer: deltas arrive, nothing moves.
            with patch.object(c, "_inject_relative", return_value=(0, 0)):
                for _ in range(200):
                    c._move_cursor(-1, -1, 40, 0)
            assert c._inward_travel == 0
            assert c._return_armed is False

            # Pointer released: real movement resumes - enter, then sweep back.
            for _ in range(10):
                c._move_cursor(-1, -1, 40, 0)
            assert c._return_armed is True
            for _ in range(10):
                c._move_cursor(-1, -1, -40, 0)
            assert c._inward_travel <= c.RETURN_RELEASE_MARGIN

            c._controller.position = (0, 500)
            c._last_move_delta = (-3, 0)
            with patch.object(c, "_clamp_cursor_to_monitor") as clamp:
                await c._check_edge()

            assert mock_stream_handler.send.called, "return-to-server never fired"
            clamp.assert_not_called()

    def test_accumulate_inward_travel_arms_after_margin(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Net inward travel past the arm margin arms - it does NOT unlock:
        the edge stays locked and the offset keeps tracking (hysteresis)."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._active_target_bbox = (0, 0, 1920, 1080)  # deterministic clamp span
        c._return_locked_edge = ScreenEdge.LEFT
        c._inward_travel = 0
        c._return_armed = False

        # Inward for a LEFT lock is +X. Below the arm margin -> not armed.
        c._accumulate_inward_travel(5, 0)
        assert c._return_armed is False
        assert c._return_locked_edge == ScreenEdge.LEFT
        # Reaching the arm margin latches armed; the edge stays locked and the
        # offset is NOT reset (it keeps tracking distance from the edge).
        c._accumulate_inward_travel(ClientMouseController.RETURN_ARM_MARGIN, 0)
        assert c._return_armed is True
        assert c._return_locked_edge == ScreenEdge.LEFT
        assert c._inward_travel == 5 + ClientMouseController.RETURN_ARM_MARGIN

    def test_accumulate_inward_travel_is_angle_agnostic(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Only the component perpendicular to the locked edge counts."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._active_target_bbox = (0, 0, 1920, 1080)  # deterministic clamp span
        c._return_locked_edge = ScreenEdge.LEFT
        c._inward_travel = 0
        c._return_armed = False

        # Pure parallel motion (along Y) contributes nothing.
        c._accumulate_inward_travel(0, 999)
        assert c._inward_travel == 0
        assert c._return_armed is False

        # Diagonal moves count only their +X (perpendicular) component; a
        # steep angle still accumulates the small inward part.
        for _ in range(6):
            c._accumulate_inward_travel(3, -50)  # +3 inward each, large parallel
        assert c._return_armed is True  # 18 >= arm margin

    def test_accumulate_inward_travel_edge_ward_jitter_reduces_offset(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """An edge-ward jitter reduces the net offset back toward the edge."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._active_target_bbox = (0, 0, 1920, 1080)  # deterministic clamp span
        c._return_locked_edge = ScreenEdge.RIGHT  # inward is -X
        c._inward_travel = 0

        c._accumulate_inward_travel(-8, 0)  # inward +8
        c._accumulate_inward_travel(8, 0)  # edge-ward, net back to 0
        assert c._inward_travel == 0
        assert c._return_locked_edge == ScreenEdge.RIGHT

    def test_accumulate_inward_travel_noop_when_unlocked(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """No lock -> accumulation is a no-op."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._return_locked_edge = None
        c._accumulate_inward_travel(100, 100)
        assert c._inward_travel == 0
        assert c._return_armed is False

    def test_accumulate_inward_travel_clamps_to_monitor_span(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Offset caps at the perpendicular monitor span (upper) and 0 (lower),
        so overshoot at a far edge can't diverge it beyond the screen."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._active_target_bbox = (0, 0, 1920, 1080)
        c._return_locked_edge = ScreenEdge.LEFT  # perpendicular axis = width
        c._inward_travel = 0
        # Overshoot far past the right edge -> capped at the 1920 width.
        c._accumulate_inward_travel(999999, 0)
        assert c._inward_travel == 1920
        assert c._return_armed is True
        # Edge-ward beyond the span -> floored at 0 (never negative).
        c._accumulate_inward_travel(-999999, 0)
        assert c._inward_travel == 0

    def test_accumulate_inward_travel_clamp_uses_perp_axis(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """A TOP/BOTTOM lock clamps to the monitor HEIGHT, not width."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._active_target_bbox = (0, 0, 1920, 1080)
        c._return_locked_edge = ScreenEdge.TOP  # perpendicular axis = height
        c._inward_travel = 0
        c._accumulate_inward_travel(0, 999999)
        assert c._inward_travel == 1080

    def test_accumulate_inward_travel_skips_clamp_on_degenerate_bbox(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """A zero-width bbox must skip the clamp, else the 0 floor would cap
        arming at 0 and the client could never hand control back."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._active_target_bbox = (0, 0, 0, 1080)  # zero width
        c._return_locked_edge = ScreenEdge.LEFT
        c._inward_travel = 0
        c._accumulate_inward_travel(ClientMouseController.RETURN_ARM_MARGIN, 0)
        assert c._inward_travel == ClientMouseController.RETURN_ARM_MARGIN
        assert c._return_armed is True

    @pytest.mark.anyio
    async def test_overshoot_then_return_reaches_release_band(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Regression: after overshooting a far edge, a full return sweep brings
        the offset back into the release band and the return fires."""
        with _ScreenGeometry(1920, 1080):
            c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
            self._activate_left_entry(c)
            # Enter and overshoot the far (RIGHT) edge many times over.
            for _ in range(200):
                c._move_cursor(-1, -1, 40, 0)  # +X inward for a LEFT lock
            assert c._inward_travel == 1920  # capped at the width, not 8000
            assert c._return_armed is True
            # One full return sweep back to the LEFT edge.
            for _ in range(200):
                c._move_cursor(-1, -1, -40, 0)
            assert c._inward_travel <= c.RETURN_RELEASE_MARGIN
            # Deliberate push onto the edge now hands control back.
            c._controller.position = (0, 500)
            c._last_move_delta = (-3, 0)
            await c._check_edge()
            assert mock_stream_handler.send.called

    def test_resolve_entry_edge_prefers_server_value(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Explicit server-supplied edge wins over landing inference."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        # Coords would infer LEFT, but the server said BOTTOM -> trust it.
        assert c._resolve_entry_edge("bottom", 0.0, 0.5) == ScreenEdge.BOTTOM

    def test_resolve_entry_edge_infers_from_landing(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Old servers: infer the edge from the pinned landing axis."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        assert c._resolve_entry_edge(None, 0.0, 0.5) == ScreenEdge.LEFT
        assert c._resolve_entry_edge(None, 1.0, 0.5) == ScreenEdge.RIGHT
        assert c._resolve_entry_edge(None, 0.5, 0.0) == ScreenEdge.TOP
        assert c._resolve_entry_edge(None, 0.5, 1.0) == ScreenEdge.BOTTOM
        # No explicit landing (hotkey path) -> nothing to lock.
        assert c._resolve_entry_edge(None, -1, -1) is None

    # ---- hysteretic entry-edge return gate ----------------------------------

    _RETURN_BINDING = {
        "client_monitor_id": 0,
        "client_edge": "left",
        "client_axis_start": 0.0,
        "client_axis_end": 1.0,
        "server_edge": "right",
        "server_monitor_min_x": 0,
        "server_monitor_min_y": 0,
        "server_monitor_max_x": 1920,
        "server_monitor_max_y": 1080,
    }

    def _activate_left_entry(self, c):
        """Put ``c`` in the active state of a client entered via its LEFT edge,
        bound back to the server RIGHT edge."""
        c._is_active = True
        c._active_target_bbox = (0, 0, 1920, 1080)
        c._edge_bindings = [self._RETURN_BINDING]
        c._server_bbox = (0, 0, 1920, 1080)
        c._return_locked_edge = ScreenEdge.LEFT
        c._inward_travel = 0
        c._return_armed = False

    @pytest.mark.anyio
    async def test_entry_gate_blocks_return_at_landing(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """At landing (offset 0, not armed) a reverse jitter must NOT return."""
        with _ScreenGeometry(1920, 1080):
            c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
            self._activate_left_entry(c)
            c._controller.position = (0, 500)  # parked on the entry edge
            c._last_move_delta = (-3, 0)  # reverse jitter
            await c._check_edge()
            assert c._is_active is True
            assert not mock_stream_handler.send.called

    @pytest.mark.anyio
    async def test_entry_gate_blocks_fast_motion_false_return(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Fast-motion regression: cursor armed and far inside (offset high),
        while the laggy OS read-back still reports the entry edge -> no return.
        The lag-free ``_inward_travel``, not the read-back, gates the return."""
        with _ScreenGeometry(1920, 1080):
            c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
            self._activate_left_entry(c)
            # Fast inward flick: armed, offset well above the release margin.
            for _ in range(10):
                c._move_cursor(-1, -1, 40, 0)  # +X inward for a LEFT lock
            assert c._return_armed is True
            assert c._inward_travel > c.RETURN_RELEASE_MARGIN
            # Laggy read-back still says the edge; a reverse delta arrives.
            c._controller.position = (0, 500)
            c._last_move_delta = (-3, 0)
            await c._check_edge()
            assert c._is_active is True
            assert not mock_stream_handler.send.called

    @pytest.mark.anyio
    async def test_entry_gate_allows_deliberate_return(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """After arming, bringing the offset back to <= release lets the
        deliberate push to the edge hand control back to the server."""
        with _ScreenGeometry(1920, 1080):
            c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
            self._activate_left_entry(c)
            # Enter (arm), then travel back toward the edge until at/near it.
            for _ in range(10):
                c._move_cursor(-1, -1, 40, 0)
            assert c._return_armed is True
            for _ in range(10):
                c._move_cursor(-1, -1, -40, 0)  # back toward the LEFT edge
            assert c._inward_travel <= c.RETURN_RELEASE_MARGIN
            # Deliberate push onto the edge.
            c._controller.position = (0, 500)
            c._last_move_delta = (-3, 0)
            await c._check_edge()
            assert mock_stream_handler.send.called  # return-to-server fired

    @pytest.mark.parametrize(
        "position",
        [(0, 500), (1919, 500), (500, 0), (500, 1079), (0, 0), (1919, 1079)],
    )
    def test_clamp_leaves_a_cursor_on_the_boundary_alone(
        self, event_bus, mock_stream_handler, mock_mouse_controller, position
    ):
        """The boundary pixel is inside the monitor, so nothing to correct.

        The OS already holds the cursor there while the user pushes outward;
        nudging it inward every tick is what made the cursor bounce off the
        edge. All four sides, and the corners.
        """
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        monitor = MonitorLayout.from_bboxes([(0, 0, 1920, 1080)]).monitors[0]

        with (
            patch.object(c, "_cursor_position", return_value=position),
            patch.object(c, "_warp_cursor") as warp,
        ):
            c._clamp_cursor_to_monitor(monitor)

        warp.assert_not_called()

    @pytest.mark.parametrize(
        "position, expected",
        [
            ((-40, 500), (0, 500)),
            ((2000, 500), (1919, 500)),
            ((500, -40), (500, 0)),
            ((500, 1200), (500, 1079)),
            ((-40, 1200), (0, 1079)),
        ],
    )
    def test_clamp_pulls_back_a_cursor_that_really_left(
        self, event_bus, mock_stream_handler, mock_mouse_controller, position, expected
    ):
        """Drift onto another monitor (or a dead zone) still gets corrected.

        Landing exactly on the nearest valid pixel, not pushed further in: the
        two sides used to be asymmetric (min+1 against max-2).
        """
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        monitor = MonitorLayout.from_bboxes([(0, 0, 1920, 1080)]).monitors[0]

        with (
            patch.object(c, "_cursor_position", return_value=position),
            patch.object(c, "_warp_cursor") as warp,
        ):
            c._clamp_cursor_to_monitor(monitor)

        warp.assert_called_once_with(*expected)

    @pytest.mark.anyio
    async def test_pushing_at_a_void_edge_never_moves_the_cursor(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """The reported bug, end to end: no warp at all while pushing outward.

        The user pushes, the OS pins the cursor at the bound, and every tick we
        used to warp it a pixel inward - at 125 Hz that is the visible bounce.
        """
        with _ScreenGeometry(1920, 1080):
            c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
            c._is_active = True
            c._edge_bindings = []
            c._intra_client_bindings = []
            c._intra_by_src = {}
            c._intra_pairs = set()
            # Where the OS holds it while the user keeps pushing left.
            mock_mouse_controller.position = (0, 500)

            with patch.object(c, "_warp_cursor") as warp:
                for _ in range(30):
                    c._last_move_delta = (-8, 0)
                    await c._check_edge()

            warp.assert_not_called()
            mock_stream_handler.send.assert_not_called()

    def test_motion_bounds_follow_the_monitor_under_the_cursor(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Where the cursor can go is the monitor's box, not the desktop union.

        With a taller monitor alongside, the union extends well below the short
        one: judging against the union would call a cursor stuck at the bottom
        of the small screen "free to move", and a backend that measures
        displacement would then read the OS swallowing the delta as an app
        holding the pointer.
        """
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._monitor_layout = MonitorLayout.from_bboxes(
            [(0, 0, 1920, 1080), (1920, 0, 3840, 2160)]
        )
        c._cached_monitor = None
        c._screen_bbox = (0, 0, 3840, 2160)

        assert c._motion_bounds(500, 1000) == (0, 0, 1920, 1080)
        # Bottom of the SHORT monitor, pushing down: the OS will swallow it.
        assert c._motion_is_bounded(500, 1079, 0, 5) is True
        # Same y on the tall monitor: there is room, so immobility would be real.
        c._cached_monitor = None
        assert c._motion_is_bounded(2500, 1079, 0, 5) is False

    def test_motion_bounds_fall_back_to_the_desktop(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Off every monitor (L-shaped dead zone), the desktop union is the box."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._screen_bbox = (0, 0, 1920, 1080)

        with patch.object(c, "_find_monitor_for_cursor", return_value=None):
            assert c._motion_bounds(10, 10) == (0, 0, 1920, 1080)
            assert c._motion_is_bounded(0, 500, -5, 0) is True
            assert c._motion_is_bounded(50, 500, -5, 0) is False
            # A zero delta on an axis asks for nothing, so it cannot be unmet.
            assert c._motion_is_bounded(0, 500, 0, 0) is True

    def test_intra_warp_rearms_return_lock(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """An intra-client warp re-locks/re-arms against the destination edge."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._return_armed = True
        c._inward_travel = 99
        c._resolve_intra_client_warp = lambda *a, **k: (1, 300, 400, "left")
        c._monitor_layout = MonitorLayout.from_bboxes([(0, 0, 1920, 1080)])
        assert c._try_intra_client_warp_sync(ScreenEdge.RIGHT, 1919, 400, None)
        assert c._return_locked_edge == ScreenEdge.LEFT
        assert c._inward_travel == 0
        assert c._return_armed is False

    @pytest.mark.anyio
    async def test_mouse_event_callback_queues_event(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test that mouse event callback queues events."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )
            # Do not start worker to prevent queue consumption

            mouse_event_data = MouseEvent(
                x=100,
                y=200,
                dx=5,
                dy=10,
                button=1,
                action=MouseEvent.MOVE_ACTION,
                is_pressed=False,
            )

            message = MagicMock()
            message.payload = mouse_event_data

            controller._is_active = True

            await controller._mouse_event_callback(message)

            # Check queue has the message (not the event)
            assert controller._queue.qsize() == 1
            queued_item = await controller._queue.get()
            assert queued_item == message

    @pytest.mark.anyio
    async def test_move_cursor_absolute(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test absolute cursor movement."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with _ScreenGeometry(1920, 1080):
                controller = ClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )

                # Normalized coordinates
                controller._move_cursor(0.5, 0.6, 0, 0)

                # Should denormalize to absolute coordinates
                assert mock_mouse_controller.position == (960, 648)

    @pytest.mark.anyio
    async def test_move_cursor_relative(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test relative cursor movement."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )
            mock_mouse_controller.position = (100, 100)

            controller._move_cursor(-1, -1, 10, 20)

            mock_mouse_controller.move.assert_called_once_with(dx=10, dy=20)

    @pytest.mark.anyio
    async def test_click_press(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test mouse button press."""

        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )

            controller._click(ButtonMapping.left.value, True)

            mock_mouse_controller.press.assert_called_once()
            assert controller._pressed is True
            assert controller._is_dragging is True

    @pytest.mark.anyio
    async def test_click_release(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test mouse button release."""

        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )
            controller._pressed = True

            controller._click(ButtonMapping.left.value, False)

            mock_mouse_controller.release.assert_called_once()
            assert controller._pressed is False
            assert controller._is_dragging is False

    @pytest.mark.anyio
    async def test_rapid_clicks_all_forwarded(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """
        Regression: rapid consecutive press/release pairs must each produce a
        real press() and release() call. The previous double-click emulation
        emitted ``click(btn, 0)`` on the second fast press, dropping it.
        """
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )

            # Four fast press/release pairs in rapid succession.
            for _ in range(4):
                controller._click(ButtonMapping.left.value, True)
                controller._click(ButtonMapping.left.value, False)

            assert mock_mouse_controller.press.call_count == 4
            assert mock_mouse_controller.release.call_count == 4

    @pytest.mark.anyio
    async def test_consecutive_clicks_increment_click_count(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """
        Fast clicks on the same button must tag press/release with an
        increasing click_count so the OS recognises double/triple-click.
        Slow clicks must reset back to 1.
        """
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )

            controller._click(ButtonMapping.left.value, True)
            assert controller._click_count == 1
            controller._click(ButtonMapping.left.value, False)

            controller._click(ButtonMapping.left.value, True)
            assert controller._click_count == 2
            controller._click(ButtonMapping.left.value, False)

            controller._click(ButtonMapping.left.value, True)
            assert controller._click_count == 3
            controller._click(ButtonMapping.left.value, False)

            # Simulate a long pause beyond the multi-click window.
            controller._last_press_time -= controller.DOUBLE_CLICK_THRESHOLD + 1

            controller._click(ButtonMapping.left.value, True)
            assert controller._click_count == 1

    @pytest.mark.anyio
    async def test_different_button_resets_click_count(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Switching button within the window must reset the multi-click counter."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )

            controller._click(ButtonMapping.left.value, True)
            controller._click(ButtonMapping.left.value, False)
            controller._click(ButtonMapping.left.value, True)
            assert controller._click_count == 2
            controller._click(ButtonMapping.left.value, False)

            controller._click(ButtonMapping.right.value, True)
            assert controller._click_count == 1

    @pytest.mark.anyio
    async def test_scroll(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test mouse scroll."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )

            controller._scroll(2, -3)

            mock_mouse_controller.scroll.assert_called_once_with(2, -3)

    @pytest.mark.anyio
    async def test_check_edge_detects_left_edge(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Client LEFT edge bound to server RIGHT dispatches a return-to-server crossing."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with _ScreenGeometry(1920, 1080):
                controller = ClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                controller._is_active = True
                # Server-pushed binding: client's LEFT edge of monitor 0
                # abuts the server's RIGHT edge on (0, 0, 1920, 1080).
                controller._edge_bindings = [
                    {
                        "server_monitor_id": 0,
                        "server_edge": "right",
                        "server_axis_start": 0.0,
                        "server_axis_end": 1.0,
                        "server_monitor_min_x": 0,
                        "server_monitor_min_y": 0,
                        "server_monitor_max_x": 1920,
                        "server_monitor_max_y": 1080,
                        "client_monitor_id": 0,
                        "client_edge": "left",
                        "client_axis_start": 0.0,
                        "client_axis_end": 1.0,
                    }
                ]
                controller._server_bbox = (0, 0, 1920, 1080)

                # MOVEMENT_HISTORY_N_THRESHOLD = 6: _check_edge adds the
                # current position so we need 5 in history beforehand.
                for x in range(10, 0, -2):
                    controller._movement_history.append((x, 500))
                assert len(controller._movement_history) == 5

                mock_mouse_controller.position = (0, 500)

                await controller._check_edge()

                assert controller.command_stream.send.called

    @pytest.mark.anyio
    async def test_check_edge_ignores_when_dragging(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test that edge detection is ignored when dragging."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with _ScreenGeometry(1920, 1080):
                controller = ClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                controller._is_active = True
                controller._is_dragging = True
                controller._current_screen = ScreenPosition.RIGHT

                # Build movement history towards left edge
                for x in range(10, -1, -2):
                    controller._movement_history.append((x, 500))

                mock_mouse_controller.position = (0, 500)

                await controller._check_edge()

                # Should not have dispatched cross-screen command
                mock_stream_handler.send.assert_not_called()

    @pytest.mark.anyio
    async def test_check_edge_uses_delta_fallback_when_clamped(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """OS-clamped cursor: the last move delta drives the crossing when history stalls."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with _ScreenGeometry(1920, 1080):
                controller = ClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                controller._is_active = True
                controller._edge_bindings = [
                    {
                        "server_monitor_id": 0,
                        "server_edge": "right",
                        "server_axis_start": 0.0,
                        "server_axis_end": 1.0,
                        "server_monitor_min_x": 0,
                        "server_monitor_min_y": 0,
                        "server_monitor_max_x": 1920,
                        "server_monitor_max_y": 1080,
                        "client_monitor_id": 0,
                        "client_edge": "left",
                        "client_axis_start": 0.0,
                        "client_axis_end": 1.0,
                    }
                ]
                controller._server_bbox = (0, 0, 1920, 1080)

                # Cursor pinned at the left edge, history full of the
                # same (0, y) because previous moves all hit the bound.
                for _ in range(8):
                    controller._movement_history.append((0, 500))
                mock_mouse_controller.position = (0, 500)
                # User keeps pushing left -> last delta is leftward.
                controller._last_move_delta = (-3, 0)

                await controller._check_edge()

                assert controller.command_stream.send.called

    @pytest.mark.anyio
    async def test_check_edge_warps_intra_client(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Intra-client binding triggers an explicit warp, not a return-to-server."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            # Two-monitor layout: primary on top, secondary below.
            from utils.screen import MonitorLayout

            layout = MonitorLayout.from_bboxes(
                [(0, 0, 1920, 1080), (0, 1080, 1920, 2160)]
            )
            with (
                patch("input.mouse._base.Screen.get_size", return_value=(1920, 2160)),
                patch(
                    "input.mouse._base.Screen.get_virtual_bbox",
                    return_value=(0, 0, 1920, 2160),
                ),
                patch(
                    "input.mouse._base.Screen.get_monitor_layout",
                    return_value=layout,
                ),
            ):
                controller = ClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                controller._is_active = True
                # primary.BOTTOM <-> secondary.TOP intra-client binding.
                topology = [
                    {
                        "src_monitor_id": 0,
                        "src_edge": "bottom",
                        "src_axis_start": 0.0,
                        "src_axis_end": 1.0,
                        "dst_monitor_id": 1,
                        "dst_edge": "top",
                        "dst_axis_start": 0.0,
                        "dst_axis_end": 1.0,
                        "dst_monitor_min_x": 0,
                        "dst_monitor_min_y": 1080,
                        "dst_monitor_max_x": 1920,
                        "dst_monitor_max_y": 2160,
                    },
                ]
                controller._intra_client_bindings = topology
                controller._intra_by_src = {0: topology}
                controller._intra_pairs = {(0, 1)}
                controller._last_known_monitor_id = 0

                for y in range(1070, 1080):
                    controller._movement_history.append((960, y))
                mock_mouse_controller.position = (960, 1079)

                await controller._check_edge()

                # Warped onto the secondary monitor.
                warped_x, warped_y = mock_mouse_controller.position
                assert warped_x == 960
                assert 1080 <= warped_y < 1090
                mock_stream_handler.send.assert_not_called()

    @pytest.mark.anyio
    async def test_check_edge_clamps_on_void(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """An edge with no binding keeps control here - without touching the cursor.

        The cursor is sitting on the boundary pixel, which is where the OS holds
        it while the user pushes outward. Nudging it inward to restate that is
        what made the cursor visibly bounce off the edge.
        """
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with _ScreenGeometry(1920, 1080):
                controller = ClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                controller._is_active = True
                # No bindings -> every outer edge is void.
                controller._edge_bindings = []
                controller._intra_client_bindings = []
                controller._intra_by_src = {}
                controller._intra_pairs = set()

                for x in range(10, 0, -2):
                    controller._movement_history.append((x, 500))
                mock_mouse_controller.position = (0, 500)

                with patch.object(controller, "_warp_cursor") as warp:
                    await controller._check_edge()

                warp.assert_not_called()
                assert mock_mouse_controller.position == (0, 500)
                mock_stream_handler.send.assert_not_called()

    @pytest.mark.anyio
    async def test_position_cursor_clamps_coordinates(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Out-of-range normalized values clamp to the last on-screen pixel."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with _ScreenGeometry(1920, 1080):
                controller = ClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )

                # Normalized 2.0 > 1.0: clamp to (w-1, h-1) rather than
                # land off-screen at (3840, 2160).
                await controller._position_cursor(2.0, 2.0)
                assert mock_mouse_controller.position == (1919, 1079)

                # Negative input clamps to origin.
                await controller._position_cursor(-1.0, -1.0)
                assert mock_mouse_controller.position == (0, 0)

    @pytest.mark.anyio
    async def test_worker_processes_queue_events(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test that worker task processes queued events."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )

            # Queue a move event
            mouse_event_data = MouseEvent(
                x=100,
                y=200,
                dx=0,
                dy=0,
                button=None,
                action=MouseEvent.MOVE_ACTION,
                is_pressed=False,
            )
            await controller._queue.put(mouse_event_data)

            # Start worker
            await controller.start()
            await asyncio.sleep(0.3)  # Give time to process

            # The worker should have processed the event
            # Check that the queue is now empty (event was consumed)
            assert controller._queue.empty()

            await controller.stop()

    @pytest.mark.anyio
    async def test_movement_history_max_length(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test that movement history maintains max length."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            controller = ClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )

            # Add more than max length
            for i in range(20):
                controller._movement_history.append((i, i))

            assert len(controller._movement_history) == 5
            # Should keep most recent
            assert controller._movement_history[-1] == (19, 19)


# ============================================================================
# Runtime monitor-change handling (hotplug)
# ============================================================================


def _two_monitor_geometry():
    """Patches for a 2-monitor layout (primary on top, secondary below)."""
    layout = MonitorLayout.from_bboxes([(0, 0, 1920, 1080), (0, 1080, 1920, 2160)])
    return [
        patch("input.mouse._base.Screen.get_size", return_value=(1920, 2160)),
        patch(
            "input.mouse._base.Screen.get_virtual_bbox",
            return_value=(0, 0, 1920, 2160),
        ),
        patch("input.mouse._base.Screen.get_monitor_layout", return_value=layout),
    ]


class TestMonitorHotplug:
    """LOCAL_MONITORS_UPDATED refresh + stranded-active recovery."""

    def test_local_monitors_updated_enum_value_is_13(self):
        """Wire-stability guard: the new event keeps value 13."""
        assert BusEventType.LOCAL_MONITORS_UPDATED == 13
        # Existing values must not have been renumbered.
        assert BusEventType.CLIENT_MONITORS_UPDATED == 12

    @pytest.mark.anyio
    async def test_server_listener_refreshes_geometry(
        self,
        event_bus,
        mock_stream_handler,
    ):
        """Server listener re-reads Screen on LOCAL_MONITORS_UPDATED."""
        with _ScreenGeometry(1920, 1080):
            listener = ServerMouseListener(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
                filtering=False,
            )
        # Sanity: constructed against the single-monitor geometry.
        assert listener._screen_bbox == (0, 0, 1920, 1080)

        with ExitStack() as stack:
            for p in _two_monitor_geometry():
                stack.enter_context(p)
            await listener._on_local_monitors_updated(None)

        assert listener._screen_size == (1920, 2160)
        assert listener._screen_bbox == (0, 0, 1920, 2160)
        assert len(listener._monitor_layout.monitors) == 2

    @pytest.mark.anyio
    async def test_server_controller_refreshes_geometry(
        self,
        event_bus,
        mock_mouse_controller,
    ):
        """Server controller re-reads the virtual bbox on hotplug."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with _ScreenGeometry(1920, 1080):
                controller = ServerMouseController(event_bus)
            assert controller._screen_bbox == (0, 0, 1920, 1080)

            with ExitStack() as stack:
                for p in _two_monitor_geometry():
                    stack.enter_context(p)
                await controller._on_local_monitors_updated(None)

        assert controller._screen_bbox == (0, 0, 1920, 2160)
        assert controller._screen_size == (1920, 2160)

    @pytest.mark.anyio
    async def test_client_controller_refreshes_geometry_and_active_target(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Client controller re-resolves _active_target_bbox to a surviving monitor."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with _ScreenGeometry(1920, 1080):
                controller = ClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
            # Active on monitor 1, which still exists in the new layout.
            controller._is_active = True
            controller._active_monitor_id = 1
            controller._cached_monitor = object()

            with ExitStack() as stack:
                for p in _two_monitor_geometry():
                    stack.enter_context(p)
                await controller._on_local_monitors_updated(None)

        assert controller._cached_monitor is None
        assert len(controller._monitor_layout.monitors) == 2
        # Re-resolved to monitor 1's OS bbox.
        assert controller._active_target_bbox == (0, 1080, 1920, 2160)
        # Monitor survived -> no forced return.
        assert controller._is_active is True

    @pytest.mark.anyio
    async def test_server_forces_return_when_active_client_bindings_empty(
        self,
        event_bus,
        mock_stream_handler,
    ):
        """Empty bindings for the active client -> ACTIVE_SCREEN_CHANGED(None)."""
        with _ScreenGeometry(1920, 1080):
            listener = ServerMouseListener(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
                filtering=False,
            )
        listener._active_clients = {"c1": True}
        listener._active_client_uid = "c1"
        listener.event_bus.dispatch = AsyncMock()

        await listener._on_client_layout_updated(
            ClientLayoutUpdatedEvent(client_uid="c1", edge_bindings=[])
        )

        dispatched = [
            c.kwargs.get("event_type")
            for c in listener.event_bus.dispatch.call_args_list
        ]
        assert BusEventType.SCREEN_CHANGE_GUARD in dispatched

    @pytest.mark.anyio
    async def test_server_repushes_topology_when_active_client_keeps_bindings(
        self,
        event_bus,
        mock_stream_handler,
    ):
        """Non-empty bindings for the active client -> topology re-push, no return."""
        with _ScreenGeometry(1920, 1080):
            listener = ServerMouseListener(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
                filtering=False,
            )
        listener._active_clients = {"c1": True}
        listener._active_client_uid = "c1"
        listener.event_bus.dispatch = AsyncMock()

        bindings = [{"server_monitor_id": 0, "client_monitor_id": 0}]
        await listener._on_client_layout_updated(
            ClientLayoutUpdatedEvent(client_uid="c1", edge_bindings=bindings)
        )

        dispatched = [
            c.kwargs.get("event_type")
            for c in listener.event_bus.dispatch.call_args_list
        ]
        assert BusEventType.SCREEN_CHANGE_GUARD not in dispatched
        mock_stream_handler.send.assert_called()

    @pytest.mark.anyio
    async def test_server_no_return_for_inactive_client(
        self,
        event_bus,
        mock_stream_handler,
    ):
        """Empty bindings for a NON-active client -> no forced return."""
        with _ScreenGeometry(1920, 1080):
            listener = ServerMouseListener(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
                filtering=False,
            )
        listener._active_clients = {"c1": True}
        listener._active_client_uid = "c1"
        listener.event_bus.dispatch = AsyncMock()

        await listener._on_client_layout_updated(
            ClientLayoutUpdatedEvent(client_uid="other", edge_bindings=[])
        )

        dispatched = [
            c.kwargs.get("event_type")
            for c in listener.event_bus.dispatch.call_args_list
        ]
        assert BusEventType.ACTIVE_SCREEN_CHANGED not in dispatched

    @pytest.mark.anyio
    async def test_client_forces_return_when_active_monitor_vanishes(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Active monitor removed on the client -> forced return-to-server."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            # Start on a 2-monitor layout, active on monitor 1.
            with ExitStack() as stack:
                for p in _two_monitor_geometry():
                    stack.enter_context(p)
                controller = ClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
            controller._is_active = True
            controller._active_monitor_id = 1

            # Monitor 1 disappears (single-monitor layout, only id 0).
            with _ScreenGeometry(1920, 1080):
                await controller._on_local_monitors_updated(None)

        # Return-to-server command sent + client marked inactive.
        assert mock_stream_handler.send.await_count >= 1
        sent = mock_stream_handler.send.await_args.args[0]
        assert isinstance(sent, CrossScreenCommandEvent)
        assert controller._is_active is False

    @pytest.mark.anyio
    async def test_client_no_return_when_active_monitor_survives(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Active monitor still present -> no forced return."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with ExitStack() as stack:
                for p in _two_monitor_geometry():
                    stack.enter_context(p)
                controller = ClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                controller._is_active = True
                controller._active_monitor_id = 0
                await controller._on_local_monitors_updated(None)

        mock_stream_handler.send.assert_not_called()
        assert controller._is_active is True


# ============================================================================
# macOS ClientMouseController backend Tests
# ============================================================================


@pytest.mark.skipif(
    sys.platform != "darwin", reason="Quartz-backed macOS mouse backend"
)
class TestDarwinClientMouseController:
    """Regression cover for the macOS relative-injection backend."""

    def _make_client(self, event_bus, mock_stream_handler, mock_mouse_controller):
        from input.mouse import _darwin

        # The controller self-tests the HID path on init (see
        # ``_HIDRelativeInjector.verify``); that must not post real events - nor
        # disable the module-wide injector - during a test run.
        with (
            patch(
                "input.mouse._base.MouseController", return_value=mock_mouse_controller
            ),
            patch("input.mouse._darwin._hid_injector", MagicMock()),
        ):
            return _darwin.ClientMouseController(
                event_bus, mock_stream_handler, mock_stream_handler
            )

    # --- relative injection ------------------------------------------------
    #
    # The deltas go through IOHIDPostEvent, below
    # CGAssociateMouseAndMouseCursorPosition, so the OS decides whether the
    # cursor moves. That is what lets a game hold its pointer while typing stays
    # untouched, with no cursor-visibility guessing anywhere (three earlier
    # generations of that heuristic all froze the cursor while typing).

    def _patch_hid(self, *, ok: bool, failure=None):
        """Stand in for the module-level HID injector.

        Mirrors the real contract: ``available`` tracks the path, and
        ``take_failure`` drains the reason once so the caller reports a
        degradation per degradation, not per event.
        """
        injector = MagicMock()
        injector.post.return_value = ok
        injector.available = ok
        injector.failure = failure

        def take_failure():
            reason, injector.failure = injector.failure, None
            return reason

        injector.take_failure.side_effect = take_failure
        return patch("input.mouse._darwin._hid_injector", injector), injector

    def test_relative_motion_goes_through_the_hid_system(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """The HID path is preferred, and no CGEvent is posted when it works."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        ctx, injector = self._patch_hid(ok=True)

        with (
            ctx,
            patch("input.mouse._darwin.CGEventCreateMouseEvent") as create,
            patch.object(c, "_cursor_position", return_value=(400.0, 300.0)),
        ):
            c._inject_relative(7, -5)

        from input.mouse import _darwin

        injector.post.assert_called_once_with(7, -5, _darwin._NX_MOUSEMOVED)
        create.assert_not_called()

    def test_hid_failure_falls_back_to_cgevent_and_warns_once(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """An unavailable HID path degrades to CGEvents without per-event noise."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        ctx, injector = self._patch_hid(ok=False, failure="IOServiceOpen returned 0x…")

        with (
            ctx,
            patch("input.mouse._darwin.CGEventCreateMouseEvent") as create,
            patch("input.mouse._darwin.CGEventSetIntegerValueField"),
            patch("input.mouse._darwin.CGEventPost"),
            patch.object(c, "_cursor_position", return_value=(400.0, 300.0)),
            patch.object(c._logger, "warning") as warn,
        ):
            c._inject_relative(7, -5)
            c._inject_relative(7, -5)

        assert create.call_count == 2, "both moves must still be delivered"
        # current position + delta, since the OS is not applying it for us here
        assert create.call_args.args[2] == (407.0, 295.0)
        warn.assert_called_once()

    @pytest.mark.parametrize(
        "button, expected_type",
        [
            (ButtonMapping.left.value, "_NX_LMOUSEDRAGGED"),
            (ButtonMapping.right.value, "_NX_RMOUSEDRAGGED"),
        ],
    )
    def test_drag_motion_stays_on_hid_with_the_dragged_type(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
        button,
        expected_type,
    ):
        """A held button changes the event type, never the path.

        Falling back to a CGEvent while dragging reintroduces the absolute
        position, and that made a grabbed cursor drift again as soon as the user
        held a mouse button in a game. The HID system posts the type we ask for,
        so the drag survives on the HID path.
        """
        from input.mouse import _darwin

        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._pressed = True
        c._is_dragging = True
        c._previous_button = button
        ctx, injector = self._patch_hid(ok=True)

        with (
            ctx,
            patch("input.mouse._darwin.CGEventCreateMouseEvent") as create,
            patch.object(c, "_cursor_position", return_value=(400.0, 300.0)),
        ):
            c._inject_relative(7, -5)

        create.assert_not_called(), "no absolute position may be posted while dragging"
        injector.post.assert_called_once_with(7, -5, getattr(_darwin, expected_type))

    def test_drag_keeps_the_dragged_type_on_the_cgevent_fallback(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Without the HID path a drag must still arrive as a MouseDragged event."""
        from input.mouse import _darwin

        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._pressed = True
        c._is_dragging = True
        c._previous_button = ButtonMapping.right.value
        ctx, _ = self._patch_hid(ok=False)

        with (
            ctx,
            patch("input.mouse._darwin.CGEventCreateMouseEvent") as create,
            patch("input.mouse._darwin.CGEventSetIntegerValueField"),
            patch("input.mouse._darwin.CGEventPost"),
            patch.object(c, "_cursor_position", return_value=(400.0, 300.0)),
        ):
            c._inject_relative(7, -5)

        assert create.call_args.args[1] == _darwin.kCGEventRightMouseDragged

    def test_local_events_suppression_is_disabled_on_init(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Our own warps must not freeze our own injected motion.

        A warp suppresses local hardware events for 0.25 s, and the HID
        injection *is* local hardware input - that was the multi-second dead
        cursor at the crossing point. A failure must not stop the client.
        """
        with patch(
            "input.mouse._darwin.CGSetLocalEventsSuppressionInterval"
        ) as suppress:
            self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        suppress.assert_called_once_with(0.0)

        with patch(
            "input.mouse._darwin.CGSetLocalEventsSuppressionInterval",
            side_effect=RuntimeError("gone"),
        ):
            self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)

    def test_applied_displacement_is_measured_not_assumed(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """The reported displacement is what the cursor did, per the OS.

        Under the HID path the OS may apply the delta or withhold it entirely
        (an app holding the pointer), so it is read back rather than assumed.
        """
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        ctx, _ = self._patch_hid(ok=True)

        with ctx, patch.object(c, "_cursor_position") as pos:
            # First call has no baseline yet, then the cursor follows...
            pos.side_effect = [(100.0, 100.0), (110.0, 95.0)]
            assert c._inject_relative(10, -5) == (0, 0)
            assert c._inject_relative(10, -5) == (10, -5)

            # ...and here it does not move at all: no travel to report.
            pos.side_effect = [(110.0, 95.0), (110.0, 95.0)]
            assert c._inject_relative(10, -5) == (0, 0)
            assert c._inject_relative(10, -5) == (0, 0)

    def test_pushing_at_the_desktop_bound_is_not_a_held_pointer(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Geometric immobility must not suspend edge routing.

        A cursor against the screen edge does not move when pushed further that
        way - measured, the OS simply swallows the delta on the HID path. Read
        as "an app is holding the pointer" it would stop ``_check_edge`` exactly
        while the user pushes at the edge to hand control back to the server.
        """
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._screen_bbox = (0, 0, 1920, 1080)
        ctx, _ = self._patch_hid(ok=True)

        with (
            ctx,
            patch.object(c, "_find_monitor_for_cursor", return_value=None),
            patch.object(c, "_cursor_position", return_value=(0.0, 500.0)),
        ):
            c._inject_relative(-10, 0)  # establishes the baseline
            for _ in range(6):
                c._inject_relative(-10, 0)  # pushing left, already at x=0
                assert c._immobile_moves == 0

            # Same immobility, but with room to move: that IS a held pointer.
            with patch.object(c, "_cursor_position", return_value=(500.0, 500.0)):
                c._inject_relative(-10, 0)  # new baseline
                c._inject_relative(-10, 0)
                assert c._immobile_moves == 1

    def test_fallback_position_cannot_run_past_the_desktop(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """The CGEvent fallback must not compound past the screen edge.

        A CGEvent carries an absolute location and the read-back is the location
        we posted, not where the cursor ended up: without a clamp, ``pos + delta``
        compounds every event (measured: -600 px after 30 pushes at the left
        edge, while the visible cursor sat still at 0).
        """
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._screen_bbox = (0, 0, 1920, 1080)
        ctx, _ = self._patch_hid(ok=False)

        # The read-back follows what we post, exactly as macOS does here.
        posted = {"x": 300.0}

        with (
            ctx,
            patch("input.mouse._darwin.CGEventCreateMouseEvent") as create,
            patch("input.mouse._darwin.CGEventSetIntegerValueField"),
            patch("input.mouse._darwin.CGEventPost"),
            patch.object(c, "_find_monitor_for_cursor", return_value=None),
            patch.object(
                c, "_cursor_position", side_effect=lambda: (posted["x"], 500.0)
            ),
        ):
            for _ in range(30):
                c._inject_relative(-20, 0)
                posted["x"] = create.call_args.args[2][0]

        assert posted["x"] == 0.0, "position ran past the desktop bound"
        assert all(call.args[2][0] >= 0 for call in create.call_args_list)

    def test_immobile_cursor_counts_up_and_resets(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """A pointer the OS won't move is counted, so routing can stand down.

        While an app holds the cursor, ``_detect_edge_via_delta`` would read
        "pushing at the edge" off deltas that move nothing and then clamp -
        warping the very cursor the game is keeping still.
        """
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        ctx, _ = self._patch_hid(ok=True)

        with ctx, patch.object(c, "_cursor_position", return_value=(50.0, 50.0)):
            c._inject_relative(10, 0)  # no baseline yet: unknown, not counted
            assert c._immobile_moves == 0
            for expected in (1, 2, 3, 4):
                c._inject_relative(10, 0)
                assert c._immobile_moves == expected
            assert c._immobile_moves >= c.IMMOBILE_MOVES_BEFORE_HOLD

        # A move the OS does apply clears it immediately.
        with ctx, patch.object(c, "_cursor_position", return_value=(60.0, 50.0)):
            c._inject_relative(10, 0)
        assert c._immobile_moves == 0

    @pytest.mark.anyio
    async def test_edge_routing_stands_down_while_pointer_is_held(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """The worker skips edge detection once the immobile run is long enough.

        Both directions are asserted: a moving cursor must still be routed.
        """
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._is_active = True
        move = MouseEvent(x=-1, y=-1, dx=10, dy=0, action=MouseEvent.MOVE_ACTION)

        async def pump():
            c._running = True
            await c._queue.put(MagicMock())
            worker = asyncio.create_task(c._run_worker())
            while not c._queue.empty():
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.005)
            c._running = False
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        with (
            patch("input.mouse._base.EventMapper.get_event", return_value=move),
            patch.object(c, "_move_cursor"),
            patch.object(c, "_check_edge", new=AsyncMock()) as check,
        ):
            c._immobile_moves = 0
            await pump()
            assert check.await_count == 1, "a moving cursor must still be routed"

            check.reset_mock()
            c._immobile_moves = c.IMMOBILE_MOVES_BEFORE_HOLD
            await pump()
            check.assert_not_awaited()

    def test_warp_cursor_generates_no_event(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Absolute placement uses CGWarpMouseCursorPosition.

        pynput's position setter posts a MouseMoved CGEvent; a landing repeats
        the placement ten times, which a focused game would read as camera
        movement. The warp also drops the measurement baseline - a jump is not
        travel.
        """
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        c._last_seen_pos = (1.0, 2.0)
        c._immobile_moves = 5

        with (
            patch("input.mouse._darwin.CGWarpMouseCursorPosition") as warp,
            patch("input.mouse._darwin.CGEventPost") as post,
        ):
            c._warp_cursor(640, 480)

        warp.assert_called_once_with((640.0, 480.0))
        post.assert_not_called()
        assert c._last_seen_pos is None
        assert c._immobile_moves == 0

    def test_cursor_position_reads_the_event_system(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Reads come from the event system, not from pynput's AppKit value."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        mock_mouse_controller.position = (11, 22)

        class _Loc:
            x, y = 333.0, 444.0

        with (
            patch("input.mouse._darwin.CGEventCreate", return_value="evt"),
            patch("input.mouse._darwin.CGEventGetLocation", return_value=_Loc()) as get,
        ):
            assert c._cursor_position() == (333.0, 444.0)
        get.assert_called_once_with("evt")

    @pytest.mark.anyio
    async def test_activation_retries_a_degraded_injection_path(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """A transient HID failure must not pin the whole session to CGEvents.

        Activation is the one moment where retrying costs nothing: it is not the
        move path, and control has just arrived.
        """
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        ctx, injector = self._patch_hid(ok=False, failure="IOServiceOpen returned 0x…")

        with ctx:
            await c._on_client_active(ClientActiveEvent(client_uid="server"))
            injector.retry.assert_called_once()

            # ...and it must stay off the hot path.
            injector.retry.reset_mock()
            with patch.object(c, "_cursor_position", return_value=(1.0, 2.0)):
                c._inject_relative(3, 0)
            injector.retry.assert_not_called()
        await c.stop()

    def test_no_pointer_lock_heuristic_remains(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Structural regression guard: the visibility heuristic must stay gone.

        It froze the cursor while typing in three successive shapes; macOS
        exposes no way to tell a game's grab from AppKit's text-field auto-hide,
        so any reintroduction is a bug, not a tuning problem.
        """
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        for attr in (
            "_refresh_pointer_lock",
            "_release_pointer_lock",
            "_cursor_is_hidden",
            "_reveal_transient_hide",
            "_pointer_locked",
        ):
            assert not hasattr(c, attr), attr

    def test_inject_relative_stamps_hid_deltas(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """On the CGEvent fallback the raw dx/dy still ride in the delta fields."""
        from input.mouse import _darwin

        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        ctx, _ = self._patch_hid(ok=False)

        with (
            ctx,
            patch("input.mouse._darwin.CGEventCreateMouseEvent", return_value="evt"),
            patch("input.mouse._darwin.CGEventSetIntegerValueField") as stamp,
            patch("input.mouse._darwin.CGEventPost"),
            patch.object(c, "_cursor_position", return_value=(10.0, 10.0)),
        ):
            c._inject_relative(3, -9)

        stamped = {call.args[1]: call.args[2] for call in stamp.call_args_list}
        assert stamped[_darwin.kCGMouseEventDeltaX] == 3
        assert stamped[_darwin.kCGMouseEventDeltaY] == -9

    def test_fallback_posts_a_plain_move_to_the_hid_tap(
        self, event_bus, mock_stream_handler, mock_mouse_controller
    ):
        """Completes the fallback matrix: type, tap and source of the position."""
        from input.mouse import _darwin

        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        ctx, _ = self._patch_hid(ok=False)

        with (
            ctx,
            patch(
                "input.mouse._darwin.CGEventCreateMouseEvent", return_value="evt"
            ) as create,
            patch("input.mouse._darwin.CGEventSetIntegerValueField"),
            patch("input.mouse._darwin.CGEventPost") as post,
            patch.object(c, "_cursor_position", return_value=(10.0, 20.0)) as pos,
        ):
            c._inject_relative(3, -9)

        assert create.call_args.args[1] == _darwin.kCGEventMouseMoved
        assert create.call_args.args[2] == (13.0, 11.0)
        post.assert_called_once_with(_darwin.kCGHIDEventTap, "evt")
        assert pos.called, "the position must come from the event system"

    @pytest.mark.parametrize("failing", ["read", "post"])
    def test_fallback_of_the_fallback_is_pynput(
        self, event_bus, mock_stream_handler, mock_mouse_controller, failing
    ):
        """When even the CGEvent path can't run, the cursor must still move."""
        c = self._make_client(event_bus, mock_stream_handler, mock_mouse_controller)
        ctx, _ = self._patch_hid(ok=False)
        create = patch(
            "input.mouse._darwin.CGEventCreateMouseEvent",
            side_effect=RuntimeError("no event source"),
        )
        position = patch.object(
            c,
            "_cursor_position",
            return_value=None if failing == "read" else (1.0, 2.0),
        )

        with ctx, create, position:
            assert c._inject_relative(7, -5) == (7, -5)

        mock_mouse_controller.move.assert_called_once_with(dx=7, dy=-5)


# ============================================================================
# macOS HID injector Tests
# ============================================================================


@pytest.mark.skipif(sys.platform != "darwin", reason="IOKit-backed HID injection")
class TestDarwinHIDInjector:
    """The HID path must degrade to CGEvents loudly, and never silently.

    ``IOHIDPostEvent`` is deprecated, so every one of these failure modes is a
    plausible future - and the pointer of the whole macOS client rides on it.
    """

    def _injector(self, **kwargs):
        from input.mouse import _darwin

        return _darwin._HIDRelativeInjector(**kwargs)

    def test_forced_cgevent_mode_never_opens_iokit(self, monkeypatch):
        """The escape hatch takes exactly the same path as a real failure."""
        from input.mouse import _darwin

        monkeypatch.setenv(_darwin.FORCE_CGEVENT_ENV_VAR, "1")
        injector = self._injector(forced_off=_darwin._forced_cgevent_reason())

        with patch("ctypes.CDLL") as cdll:
            assert injector.post(5, 0) is False
        cdll.assert_not_called()
        assert injector.available is False
        assert _darwin.FORCE_CGEVENT_ENV_VAR in (injector.take_failure() or "")
        # Drained: a degradation is reported once, not once per event.
        assert injector.take_failure() is None

    def test_forced_mode_is_not_retried(self, monkeypatch):
        """An explicit opt-out must survive activation retries."""
        from input.mouse import _darwin

        monkeypatch.setenv(_darwin.FORCE_CGEVENT_ENV_VAR, "1")
        injector = self._injector(forced_off=_darwin._forced_cgevent_reason())

        assert injector.retry(lambda: (0.0, 0.0)) is False
        assert injector.available is False

    def test_env_var_is_strict_about_its_value(self, monkeypatch):
        """Same convention as PERPETUA_DAEMON_FORCE_EXIT: only "1" opts in."""
        from input.mouse import _darwin

        for value in ("0", "true", "yes", ""):
            monkeypatch.setenv(_darwin.FORCE_CGEVENT_ENV_VAR, value)
            assert _darwin._forced_cgevent_reason() is None, value
        monkeypatch.setenv(_darwin.FORCE_CGEVENT_ENV_VAR, "1")
        assert _darwin._forced_cgevent_reason() is not None

    def test_self_test_disables_when_the_cursor_does_not_move(self):
        """The failure mode a deprecated API really has: a silent no-op.

        A motionless cursor is indistinguishable from an app holding the
        pointer, so without this probe the fallback would never engage and the
        cursor would just stay dead.
        """
        injector = self._injector()

        with patch.object(injector, "post", return_value=True) as post:
            assert injector.verify(lambda: (100.0, 100.0)) is False

        assert injector.available is False
        assert "did not move" in (injector.take_failure() or "")
        # It still put the probe delta back before giving up.
        assert [call.args for call in post.call_args_list] == [(1, 0), (-1, 0)]

    def test_self_test_passes_and_restores_the_cursor(self):
        """A working path stays available and leaves the cursor where it was."""
        injector = self._injector()
        positions = iter([(100.0, 100.0), (101.0, 100.0)])

        with patch.object(injector, "post", return_value=True) as post:
            assert injector.verify(lambda: next(positions)) is True

        assert injector.available is True
        assert injector.take_failure() is None
        assert [call.args for call in post.call_args_list] == [(1, 0), (-1, 0)]

    def test_self_test_waits_for_the_position_to_catch_up(self):
        """The read lags the injection, so one immediate look is not enough.

        Measured on Darwin 25.5: still unchanged 2 ms after the post, changed by
        10 ms. Judging on the first read would disable a healthy HID path and
        silently give up the game fidelity it exists for.
        """
        injector = self._injector()
        # Stale, stale, stale, then the pixel finally lands.
        positions = iter([(100.0, 100.0)] * 4 + [(101.0, 100.0)])

        with patch.object(injector, "post", return_value=True):
            assert injector.verify(lambda: next(positions)) is True
        assert injector.available is True

    def test_self_test_gives_up_after_its_timeout(self):
        """The polling must be bounded - it runs on the loop at activation."""
        injector = self._injector()

        with patch.object(injector, "post", return_value=True):
            started = time.perf_counter()
            assert injector.verify(lambda: (100.0, 100.0)) is False
            elapsed = time.perf_counter() - started

        assert elapsed < injector.SELF_TEST_TIMEOUT * 4, "self-test must not hang"
        assert "did not move" in (injector.take_failure() or "")

    def test_self_test_needs_a_readable_position(self):
        injector = self._injector()
        assert injector.verify(lambda: None) is False
        assert injector.available is False
        assert "self-test" in (injector.take_failure() or "")

    @pytest.mark.parametrize(
        "break_at, expected",
        [
            ("matching", "IOServiceMatching"),
            ("service", "service not found"),
            ("open", "IOServiceOpen returned"),
            ("raise", "IOServiceOpen failed"),
        ],
    )
    def test_every_open_failure_records_a_reason(self, break_at, expected):
        """No silent degradation: each way IOKit can fail names itself."""
        injector = self._injector()
        iokit = MagicMock()
        iokit.IOServiceMatching.return_value = 0 if break_at == "matching" else 1234
        iokit.IOServiceGetMatchingService.return_value = (
            0 if break_at == "service" else 99
        )
        iokit.IOServiceOpen.return_value = 0xE00002C1 if break_at == "open" else 0
        if break_at == "raise":
            iokit.IOServiceOpen.side_effect = RuntimeError("boom")

        # Only IOKit is faked: the libSystem lookup for mach_task_self_ is real,
        # so the failure under test is the only thing that fails.
        import ctypes as _ctypes

        real_cdll = _ctypes.CDLL

        def cdll(path, *args, **kwargs):
            if "IOKit" in str(path):
                return iokit
            return real_cdll(path, *args, **kwargs)

        with patch("ctypes.CDLL", side_effect=cdll):
            assert injector.post(1, 0) is False

        assert injector.available is False
        reason = injector.take_failure()
        assert reason and expected in reason
        assert injector.take_failure() is None

    def test_post_failure_records_a_reason(self):
        """A non-zero IOReturn, or a raising call, hands over to the fallback."""
        for setup, expected in (
            (
                lambda k: setattr(
                    k, "IOHIDPostEvent", MagicMock(return_value=0xE00002C7)
                ),
                "returned 0x",
            ),
            (
                lambda k: setattr(
                    k, "IOHIDPostEvent", MagicMock(side_effect=OSError("x"))
                ),
                "failed - OSError",
            ),
        ):
            injector = self._injector()
            injector._iokit = MagicMock()
            injector._opened = True
            injector._connect = 7
            setup(injector._iokit)

            assert injector.post(1, 0) is False
            assert injector.available is False
            assert expected in (injector.take_failure() or "")

    def test_missing_handle_degrades_with_a_reason(self):
        """The one branch that used to degrade silently.

        ``available`` was cleared without recording why, so the caller - which
        only warns when there is a reason - stayed quiet about running on the
        fallback.
        """
        injector = self._injector()
        injector._opened = True  # "already opened" but no handle: never silent
        injector._iokit = None

        assert injector.post(1, 0) is False
        assert injector.available is False
        assert injector.take_failure(), "degradation must name itself"

    def test_closed_connection_is_not_posted_to(self):
        """After close() a post must degrade, not target io_connect_t 0."""
        injector = self._injector()
        injector._iokit = MagicMock()
        injector._opened = True
        injector._connect = 7

        injector.close()

        assert injector.available is False
        assert injector._iokit is None
        assert injector.post(1, 0) is False

    def test_retry_reopens_a_degraded_path(self):
        """A transient failure at daemon start must not pin the whole session."""
        injector = self._injector()
        injector._opened = True
        injector._disable("IOServiceOpen returned 0xE00002C1")
        assert injector.available is False

        with patch.object(injector, "verify", return_value=True) as verify:
            assert injector.retry(lambda: (0.0, 0.0)) is True

        verify.assert_called_once()
        assert injector._opened is False, "a retry must re-open, not reuse the handle"
        assert injector.available is True

    def test_retry_is_a_no_op_while_the_path_works(self):
        """Nothing to recover: activation must not disturb a healthy path."""
        injector = self._injector()

        with patch.object(injector, "verify") as verify:
            assert injector.retry(lambda: (0.0, 0.0)) is True

        verify.assert_not_called()
