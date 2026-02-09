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

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from event import (
    MouseEvent,
    ActiveScreenChangedEvent,
    ClientConnectedEvent,
    ClientDisconnectedEvent,
    ClientActiveEvent,
)
from input.mouse._base import (
    EdgeDetector,
    ScreenEdge,
    ServerMouseListener,
    ServerMouseController,
    ClientMouseController,
    ButtonMapping,
)
from model.client import ScreenPosition
from network.stream import StreamType


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

    def test_get_crossing_coords_bottom_to_top(self, screen_size):
        """Test crossing coordinates from bottom edge to top position."""
        x, y = EdgeDetector.get_crossing_coords(
            500, 1079, screen_size, ScreenEdge.BOTTOM, ScreenPosition.TOP
        )
        assert x == pytest.approx(500 / 1920)
        assert y == 0.0

    def test_get_crossing_coords_top_to_bottom(self, screen_size):
        """Test crossing coordinates from top edge to bottom position."""
        x, y = EdgeDetector.get_crossing_coords(
            500, 0, screen_size, ScreenEdge.TOP, ScreenPosition.BOTTOM
        )
        assert x == pytest.approx(500 / 1920)
        assert y == 1.0

    def test_get_crossing_coords_left_to_right(self, screen_size):
        """Test crossing coordinates from left edge to right position."""
        x, y = EdgeDetector.get_crossing_coords(
            0, 500, screen_size, ScreenEdge.LEFT, ScreenPosition.RIGHT
        )
        assert x == 1.0
        assert y == pytest.approx(500 / 1080)

    def test_get_crossing_coords_right_to_left(self, screen_size):
        """Test crossing coordinates from right edge to left position."""
        x, y = EdgeDetector.get_crossing_coords(
            1919, 500, screen_size, ScreenEdge.RIGHT, ScreenPosition.LEFT
        )
        assert x == 0.0
        assert y == pytest.approx(500 / 1080)

    def test_get_crossing_coords_invalid_screen(self, screen_size):
        """Test invalid screen returns -1, -1."""
        x, y = EdgeDetector.get_crossing_coords(
            500, 0, screen_size, ScreenEdge.TOP, None
        )
        assert x == -1
        assert y == -1

    def test_get_crossing_coords_empty_screen(self, screen_size):
        """Test empty screen returns -1, -1."""
        x, y = EdgeDetector.get_crossing_coords(500, 0, screen_size, ScreenEdge.TOP, "")
        assert x == -1
        assert y == -1

    def test_get_crossing_coords_mismatched_edge_position(self, screen_size):
        """Test mismatched edge and position returns -1, -1."""
        x, y = EdgeDetector.get_crossing_coords(
            500, 0, screen_size, ScreenEdge.BOTTOM, ScreenPosition.BOTTOM
        )
        assert x == -1
        assert y == -1


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
            client_screen="client1",
            streams=[StreamType.MOUSE, StreamType.KEYBOARD],
        )

        await listener._on_client_connected(event)

        assert "client1" in listener._active_screens
        assert listener._active_screens["client1"] is True

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
            client_screen="client1",
            streams=[StreamType.KEYBOARD],  # No MOUSE stream
        )

        await listener._on_client_connected(event)

        assert "client1" not in listener._active_screens

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
            listener._active_screens["client1"] = True
            listener._listening = True

            event = ClientDisconnectedEvent(
                client_screen="client1",
                streams=[StreamType.MOUSE],
            )

            await listener._on_client_disconnected(event)

            assert "client1" not in listener._active_screens

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
            listener._active_screens["client1"] = True
            listener._listening = True

            event = ClientDisconnectedEvent(
                client_screen="client1",
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
    async def test_on_click_sends_event_when_listening(
        self,
        event_bus,
        mock_stream_handler,
    ):
        """Test that click events are sent when listening."""
        from pynput.mouse import Button

        listener = ServerMouseListener(
            event_bus,
            mock_stream_handler,
            mock_stream_handler,
            filtering=False,
        )
        listener._listening = True

        with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
            listener._screen_size = (1920, 1080)
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

            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
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
            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
                controller = ServerMouseController(event_bus)

                # Coordinates are normalized (0-1 range)
                controller.position_cursor(0.5, 0.6)

                # Should denormalize to absolute coordinates
                assert mock_mouse_controller.position == (960, 648)

    @pytest.mark.anyio
    async def test_position_cursor_clamps_to_screen_bounds(
        self, event_bus, mock_mouse_controller
    ):
        """Test that position_cursor clamps coordinates to screen bounds."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
                controller = ServerMouseController(event_bus)

                # Test normalized values beyond 1.0
                controller.position_cursor(2.0, 2.0)

                # Should denormalize: 2.0 * 1920 = 3840, 2.0 * 1080 = 2160
                assert mock_mouse_controller.position == (3840, 2160)


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

            event = ClientActiveEvent(client_screen="server")

            await controller._on_client_active(event)

            assert controller._is_active is True
            assert controller._running is True

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

            event = ClientActiveEvent(client_screen="server")

            await controller._on_client_inactive(event)

            assert controller._is_active is False
            assert len(controller._movement_history) == 0

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
                is_presed=False,
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
            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
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
        """Test edge detection on left edge."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
                controller = ClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                controller._is_active = True
                controller._current_screen = ScreenPosition.RIGHT

                # Build enough movement history (MOVEMENT_HISTORY_N_THRESHOLD = 6)
                # _check_edge will add current position, so we need 5 in history
                for x in range(10, 0, -2):
                    controller._movement_history.append((x, 500))

                # Ensure we have 5 positions in history
                assert len(controller._movement_history) == 5

                # Set controller position to edge (will be added as 6th position)
                mock_mouse_controller.position = (0, 500)

                await controller._check_edge()

                # Should have dispatched cross-screen command
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
            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
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
    async def test_position_cursor_clamps_coordinates(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test that position_cursor clamps coordinates to screen bounds."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
                controller = ClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )

                # Normalized coordinates beyond 1.0
                await controller._position_cursor(2.0, 2.0)

                # Should denormalize: 2.0 * 1920 = 3840, 2.0 * 1080 = 2160
                assert mock_mouse_controller.position == (3840, 2160)

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
                is_presed=False,
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

            # Should maintain max length (MOVEMENT_HISTORY_LEN = 8)
            assert len(controller._movement_history) == 8
            # Should keep most recent
            assert controller._movement_history[-1] == (19, 19)


# ============================================================================
# macOS-specific ClientMouseController Tests
# ============================================================================


class TestClientMouseControllerDarwin:
    """Test ClientMouseController macOS-specific functionality."""

    @pytest.mark.anyio
    async def test_move_cursor_relative_clamps_left_edge(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test relative cursor movement is clamped when at left edge moving left."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
                from input.mouse._darwin import ClientMouseController as DarwinClientMouseController

                controller = DarwinClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                # Position cursor beyond left edge
                mock_mouse_controller.position = (-5, 500)

                # Try to move further left
                controller._move_cursor(-1, -1, -10, 5)

                # dx should be clamped to 0, dy should remain
                mock_mouse_controller.move.assert_called_once_with(dx=0, dy=5)

    @pytest.mark.anyio
    async def test_move_cursor_relative_clamps_right_edge(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test relative cursor movement is clamped when at right edge moving right."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
                from input.mouse._darwin import ClientMouseController as DarwinClientMouseController

                controller = DarwinClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                # Position cursor beyond right edge
                mock_mouse_controller.position = (1925, 500)

                # Try to move further right
                controller._move_cursor(-1, -1, 10, 5)

                # dx should be clamped to 0, dy should remain
                mock_mouse_controller.move.assert_called_once_with(dx=0, dy=5)

    @pytest.mark.anyio
    async def test_move_cursor_relative_clamps_top_edge(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test relative cursor movement is clamped when at top edge moving up."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
                from input.mouse._darwin import ClientMouseController as DarwinClientMouseController

                controller = DarwinClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                # Position cursor beyond top edge
                mock_mouse_controller.position = (500, -3)

                # Try to move further up
                controller._move_cursor(-1, -1, 5, -10)

                # dy should be clamped to 0, dx should remain
                mock_mouse_controller.move.assert_called_once_with(dx=5, dy=0)

    @pytest.mark.anyio
    async def test_move_cursor_relative_clamps_bottom_edge(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test relative cursor movement is clamped when at bottom edge moving down."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
                from input.mouse._darwin import ClientMouseController as DarwinClientMouseController

                controller = DarwinClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                # Position cursor beyond bottom edge
                mock_mouse_controller.position = (500, 1085)

                # Try to move further down
                controller._move_cursor(-1, -1, 5, 10)

                # dy should be clamped to 0, dx should remain
                mock_mouse_controller.move.assert_called_once_with(dx=5, dy=0)

    @pytest.mark.anyio
    async def test_move_cursor_relative_allows_recovery_from_left(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test relative cursor movement is allowed when moving back from left edge."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
                from input.mouse._darwin import ClientMouseController as DarwinClientMouseController

                controller = DarwinClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                # Position cursor beyond left edge
                mock_mouse_controller.position = (-5, 500)

                # Move right (recovery direction)
                controller._move_cursor(-1, -1, 10, 5)

                # Both dx and dy should be allowed
                mock_mouse_controller.move.assert_called_once_with(dx=10, dy=5)

    @pytest.mark.anyio
    async def test_move_cursor_relative_allows_recovery_from_right(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test relative cursor movement is allowed when moving back from right edge."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
                from input.mouse._darwin import ClientMouseController as DarwinClientMouseController

                controller = DarwinClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                # Position cursor beyond right edge
                mock_mouse_controller.position = (1925, 500)

                # Move left (recovery direction)
                controller._move_cursor(-1, -1, -10, 5)

                # Both dx and dy should be allowed
                mock_mouse_controller.move.assert_called_once_with(dx=-10, dy=5)

    @pytest.mark.anyio
    async def test_move_cursor_relative_clamps_multiple_axes(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test relative cursor movement clamps both axes when out of bounds."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            with patch("input.mouse._base.Screen.get_size", return_value=(1920, 1080)):
                from input.mouse._darwin import ClientMouseController as DarwinClientMouseController

                controller = DarwinClientMouseController(
                    event_bus,
                    mock_stream_handler,
                    mock_stream_handler,
                )
                # Position cursor beyond left and top edges
                mock_mouse_controller.position = (-5, -3)

                # Try to move further left and up
                controller._move_cursor(-1, -1, -10, -10)

                # Both dx and dy should be clamped to 0
                mock_mouse_controller.move.assert_called_once_with(dx=0, dy=0)

    @pytest.mark.anyio
    async def test_move_cursor_relative_handles_invalid_delta(
        self,
        event_bus,
        mock_stream_handler,
        mock_mouse_controller,
    ):
        """Test relative cursor movement handles invalid delta values."""
        with patch(
            "input.mouse._base.MouseController", return_value=mock_mouse_controller
        ):
            from input.mouse._darwin import ClientMouseController as DarwinClientMouseController

            controller = DarwinClientMouseController(
                event_bus,
                mock_stream_handler,
                mock_stream_handler,
            )
            mock_mouse_controller.position = (100, 100)

            # Pass non-numeric values
            controller._move_cursor(-1, -1, "invalid", "also_invalid")

            # Should fallback to 0, 0
            mock_mouse_controller.move.assert_called_once_with(dx=0, dy=0)
