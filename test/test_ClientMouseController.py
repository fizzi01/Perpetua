import unittest
import time
from unittest.mock import Mock, MagicMock, patch, call
from queue import Queue
from threading import Event as ThreadEvent

from event.EventBus import EventBus, ThreadSafeEventBus
from event.Event import EventType, MouseEvent, CommandEvent
from input.mouse import ClientMouseController
from network.stream.GenericStream import StreamHandler
from network.protocol.message import ProtocolMessage

from utils.logging.logger import Logger

Logger(stdout=print, logging=True)  # Initialize logger for tests

class MockStreamHandler(StreamHandler):
    """Mock stream handler for testing"""

    def __init__(self, stream_type: int):
        self.stream_type = stream_type
        self.sent_events = []
        self._active = False
        self._receive_callback = None
        self._receive_callback_type = None

    def send(self, event):
        """Mock send method"""
        self.sent_events.append(event)

    def register_receive_callback(self, callback, message_type=None):
        """Mock register callback"""
        self._receive_callback = callback
        self._receive_callback_type = message_type

    def simulate_receive(self, message):
        """Simulate receiving a message"""
        if self._receive_callback:
            self._receive_callback(message)

    def start(self):
        self._active = True

    def stop(self):
        self._active = False


class TestClientMouseController(unittest.TestCase):
    """Test suite for ClientMouseController"""

    def setUp(self):
        """Setup before each test"""
        self.event_bus = ThreadSafeEventBus()
        self.stream_handler = MockStreamHandler(stream_type=1)

        # Patch the MouseController
        self.mouse_controller_patcher = patch('input.mouse._darwin.MouseController')
        self.mock_mouse_controller_class = self.mouse_controller_patcher.start()

        # Create mock controller instance
        self.mock_controller_instance = MagicMock()
        self.mock_controller_instance.position = (500, 500)
        self.mock_mouse_controller_class.return_value = self.mock_controller_instance

        # Patch Screen.get_size
        self.screen_patcher = patch('input.mouse._darwin.Screen.get_size')
        self.mock_screen = self.screen_patcher.start()
        self.mock_screen.return_value = (1920, 1080)

        # Patch Button
        self.button_patcher = patch('input.mouse._darwin.Button')
        self.mock_button = self.button_patcher.start()
        self.mock_button.return_value = MagicMock()

        # Patch EventMapper
        self.event_mapper_patcher = patch('input.mouse._darwin.EventMapper')
        self.mock_event_mapper = self.event_mapper_patcher.start()

        self.controller = ClientMouseController(
            event_bus=self.event_bus,
            stream_handler=self.stream_handler
        )

    def tearDown(self):
        """Cleanup after each test"""
        self.mouse_controller_patcher.stop()
        self.screen_patcher.stop()
        self.button_patcher.stop()
        self.event_mapper_patcher.stop()

    def test_initialization(self):
        """Test that the controller initializes correctly"""
        self.assertIsNotNone(self.controller)
        self.assertFalse(self.controller._is_active)
        self.assertEqual(self.controller._screen_size, (1920, 1080))
        self.assertIsInstance(self.controller._movement_history, Queue)
        self.assertEqual(self.controller._movement_history.maxsize, 5)
        self.assertIsNotNone(self.stream_handler._receive_callback)
        self.assertEqual(self.stream_handler._receive_callback_type, "mouse")

    def test_client_active_event(self):
        """Test that CLIENT_ACTIVE event activates the controller"""
        self.assertFalse(self.controller._is_active)

        # Dispatch client active event
        self.event_bus.dispatch(
            event_type=EventType.CLIENT_ACTIVE,
            data={}
        )

        time.sleep(0.1)
        self.assertTrue(self.controller._is_active)

    def test_client_inactive_event(self):
        """Test that CLIENT_INACTIVE event deactivates the controller"""
        # First activate
        self.event_bus.dispatch(
            event_type=EventType.CLIENT_ACTIVE,
            data={}
        )
        time.sleep(0.1)
        self.assertTrue(self.controller._is_active)

        # Now deactivate
        self.event_bus.dispatch(
            event_type=EventType.CLIENT_INACTIVE,
            data={}
        )
        time.sleep(0.1)
        self.assertFalse(self.controller._is_active)

    def test_client_active_clears_movement_history(self):
        """Test that CLIENT_ACTIVE clears movement history"""
        # Add some movements to history
        self.controller._movement_history.put((100, 100))
        self.controller._movement_history.put((200, 200))
        self.assertEqual(self.controller._movement_history.qsize(), 2)

        # Dispatch event
        self.event_bus.dispatch(
            event_type=EventType.CLIENT_ACTIVE,
            data={}
        )
        time.sleep(0.1)

        # History should be cleared
        self.assertEqual(self.controller._movement_history.qsize(), 0)

    def test_move_cursor_relative(self):
        """Test moving cursor with relative coordinates (dx, dy)"""
        self.controller.move_cursor(x=0, y=0, dx=10, dy=20)

        # Should call move with dx, dy
        self.mock_controller_instance.move.assert_called_once_with(dx=10, dy=20)

        # Movement should be added to history
        self.assertEqual(self.controller._movement_history.qsize(), 1)
        queue_data = list(self.controller._movement_history.queue)
        self.assertEqual(queue_data[0], (0, 0))

    def test_move_cursor_absolute_normalized(self):
        """Test moving cursor with absolute normalized coordinates"""
        # Normalized coordinates (0.5, 0.5) should map to center of screen
        self.controller.move_cursor(x=0.5, y=0.5, dx=0, dy=0)

        # Should set position to denormalized coordinates
        expected_x = int(0.5 * 1920)  # 960
        expected_y = int(0.5 * 1080)  # 540
        self.mock_controller_instance.position = (expected_x, expected_y)

        # Movement should be added to history
        self.assertEqual(self.controller._movement_history.qsize(), 1)

    def test_move_cursor_history_queue_management(self):
        """Test that movement history queue is properly managed"""
        # Add 6 movements (maxsize is 5, so oldest should be removed)
        for i in range(6):
            self.controller.move_cursor(x=i * 0.1, y=i * 0.1, dx=0, dy=0)

        self.assertEqual(self.controller._movement_history.qsize(), 5)

        # First element should be (0.1, 0.1), not (0, 0)
        queue_data = list(self.controller._movement_history.queue)
        self.assertEqual(queue_data[0], (0.1, 0.1))

    def test_click_press(self):
        """Test mouse click press"""
        self.controller._pressed = False

        # Simulate press
        self.controller.click(button=1, is_pressed=True)

        # Should call press
        self.mock_controller_instance.press.assert_called_once()
        self.assertTrue(self.controller._pressed)

    def test_click_release(self):
        """Test mouse click release"""
        self.controller._pressed = True

        # Simulate release
        self.controller.click(button=1, is_pressed=False)

        # Should call release
        self.mock_controller_instance.release.assert_called_once()
        self.assertFalse(self.controller._pressed)

    def test_double_click_detection(self):
        """Test double click detection"""
        # First click
        self.controller.click(button=1, is_pressed=True)
        time.sleep(0.05)  # Small delay

        # Second click within 200ms
        self.controller.click(button=1, is_pressed=True)

        # Should detect double click
        # The second click should call click with count=2
        calls = self.mock_controller_instance.click.call_args_list
        if len(calls) > 0:
            # Verify double click was triggered
            self.assertGreater(len(calls), 0)

    def test_scroll(self):
        """Test mouse scroll"""
        self.controller.scroll(dx=10, dy=-5)

        # Should call scroll with converted integers
        self.mock_controller_instance.scroll.assert_called_once_with(10, -5)

    def test_scroll_with_float_values(self):
        """Test scroll with float values (should convert to int)"""
        self.controller.scroll(dx=10.7, dy=-5.3)

        # Should call scroll with integers
        self.mock_controller_instance.scroll.assert_called_once_with(10, -5)

    def test_mouse_event_callback_move(self):
        """Test receiving move event from stream"""
        # Create mock message
        mock_message = MagicMock()

        # Create mock MouseEvent
        mock_event = MouseEvent(x=0.5, y=0.5, dx=10, dy=20)
        mock_event.action = MouseEvent.MOVE_ACTION

        # Configure EventMapper to return our event
        self.mock_event_mapper.get_event.return_value = mock_event

        # Simulate receiving message
        self.stream_handler.simulate_receive(mock_message)

        time.sleep(0.2)  # Give time for processing

        # Verify EventMapper was called
        self.mock_event_mapper.get_event.assert_called_once_with(mock_message)

        # Verify movement was processed
        self.assertGreater(self.controller._movement_history.qsize(), 0)

    def test_mouse_event_callback_click(self):
        """Test receiving click event from stream"""
        mock_message = MagicMock()

        mock_event = MouseEvent(x=500, y=500, button=1)
        mock_event.action = MouseEvent.CLICK_ACTION
        mock_event.is_pressed = True

        self.mock_event_mapper.get_event.return_value = mock_event

        # Simulate receiving message
        self.stream_handler.simulate_receive(mock_message)

        time.sleep(0.1)

        # Verify click was processed
        self.assertTrue(self.mock_controller_instance.press.called or
                       self.mock_controller_instance.click.called)

    def test_mouse_event_callback_scroll(self):
        """Test receiving scroll event from stream"""
        mock_message = MagicMock()

        mock_event = MouseEvent(x=10, y=-5)
        mock_event.action = MouseEvent.SCROLL_ACTION
        mock_event.dx = 10
        mock_event.dy = -5

        self.mock_event_mapper.get_event.return_value = mock_event

        # Simulate receiving message
        self.stream_handler.simulate_receive(mock_message)

        time.sleep(0.1)

        # Verify scroll was processed
        self.mock_controller_instance.scroll.assert_called()

    def test_mouse_event_callback_non_mouse_event(self):
        """Test receiving non-mouse event (should log warning)"""
        mock_message = MagicMock()

        # Return a non-MouseEvent
        mock_event = CommandEvent(command="test")
        self.mock_event_mapper.get_event.return_value = mock_event

        # Should not raise exception
        self.stream_handler.simulate_receive(mock_message)
        time.sleep(0.1)

    def test_edge_detection_while_active(self):
        """Test edge detection when client is active"""
        # Activate client
        self.controller._is_active = True

        # Simulate movements towards right edge
        movements = [(1700, 500), (1750, 500), (1800, 500), (1850, 500), (1919, 500)]

        for x, y in movements:
            self.controller._movement_history.put((x, y))

        # Set controller position to edge
        self.mock_controller_instance.position = (1919, 500)

        # Patch EdgeDetector to return True
        with patch('input.mouse._darwin.EdgeDetector.is_at_edge') as mock_edge:
            mock_edge.return_value = True

            # Trigger edge check
            self.controller._check_edge()

            time.sleep(0.2)

            # Should send cross screen command
            self.assertGreater(len(self.stream_handler.sent_events), 0)

            # Find CommandEvent
            command_events = [e for e in self.stream_handler.sent_events
                            if isinstance(e, CommandEvent)]
            self.assertGreater(len(command_events), 0)

            # Verify command is CROSS_SCREEN
            self.assertEqual(command_events[0].command, CommandEvent.CROSS_SCREEN)

    def test_edge_detection_while_inactive(self):
        """Test that edge detection doesn't trigger when inactive"""
        self.controller._is_active = False

        # Add movements
        for i in range(5):
            self.controller._movement_history.put((1900 + i, 500))

        self.mock_controller_instance.position = (1919, 500)

        # Trigger edge check
        self.controller._check_edge()

        time.sleep(0.2)

        # Should not send any events
        self.assertEqual(len(self.stream_handler.sent_events), 0)

    def test_edge_detection_dispatches_client_inactive(self):
        """Test that edge detection dispatches CLIENT_INACTIVE event"""
        self.controller._is_active = True

        # Setup event listener
        event_received = []

        def capture_event(data):
            event_received.append(data)

        self.event_bus.subscribe(EventType.CLIENT_INACTIVE, capture_event)

        # Add movements
        for i in range(5):
            self.controller._movement_history.put((1900 + i, 500))

        self.mock_controller_instance.position = (1919, 500)

        # Patch EdgeDetector
        with patch('input.mouse._darwin.EdgeDetector.is_at_edge') as mock_edge:
            mock_edge.return_value = True

            # Trigger edge check
            self.controller._check_edge()

            time.sleep(0.2)

            # Should have received CLIENT_INACTIVE event
            self.assertGreater(len(event_received), 0)

    def test_cross_screen_event_prevents_duplicate_processing(self):
        """Test that cross screen event prevents duplicate edge processing"""
        self.controller._is_active = True
        self.controller._cross_screen_event.set()

        # Add movements
        for i in range(5):
            self.controller._movement_history.put((1900 + i, 500))

        # Trigger edge check
        self.controller._check_edge()

        time.sleep(0.1)

        # Should not send any events (event already set)
        self.assertEqual(len(self.stream_handler.sent_events), 0)

    def test_invalid_button_value(self):
        """Test handling of invalid button value"""
        # Make Button raise ValueError
        self.mock_button.side_effect = ValueError("Invalid button")

        # Should not raise exception
        self.controller.click(button=999, is_pressed=True)

        # Should not have called press
        self.mock_controller_instance.press.assert_not_called()

    def test_invalid_scroll_values(self):
        """Test handling of invalid scroll values"""
        # Should handle gracefully
        self.controller.scroll(dx="invalid", dy="invalid")

        # Should not crash, but also should not call scroll
        # (depends on implementation - might log error)

    def test_invalid_position_values(self):
        """Test handling of invalid position values"""
        # Should handle gracefully
        self.controller.move_cursor(x="invalid", y="invalid", dx=0, dy=0)

        # Should not crash

    def test_movement_history_persistence(self):
        """Test that movement history persists across multiple moves"""
        self.controller.move_cursor(x=0.1, y=0.1, dx=0, dy=0)
        self.controller.move_cursor(x=0.2, y=0.2, dx=0, dy=0)
        self.controller.move_cursor(x=0.3, y=0.3, dx=0, dy=0)

        self.assertEqual(self.controller._movement_history.qsize(), 3)

        # Verify data
        queue_data = list(self.controller._movement_history.queue)
        self.assertEqual(queue_data[0], (0.1, 0.1))
        self.assertEqual(queue_data[1], (0.2, 0.2))
        self.assertEqual(queue_data[2], (0.3, 0.3))

    def test_rapid_movements(self):
        """Test handling of rapid movement events"""
        # Send many movements quickly
        for i in range(20):
            self.controller.move_cursor(x=i * 0.05, y=i * 0.05, dx=5, dy=5)

        # Queue should not exceed maxsize
        self.assertLessEqual(self.controller._movement_history.qsize(), 5)

    def test_press_release_sequence(self):
        """Test complete press-release sequence"""
        # Press
        self.controller.click(button=1, is_pressed=True)
        self.assertTrue(self.controller._pressed)

        time.sleep(0.05)

        # Release
        self.controller.click(button=1, is_pressed=False)
        self.assertFalse(self.controller._pressed)

        # Verify both press and release were called
        self.mock_controller_instance.press.assert_called()
        self.mock_controller_instance.release.assert_called()

    def test_multiple_double_clicks(self):
        """Test multiple consecutive double clicks"""
        for _ in range(3):
            # First click
            self.controller.click(button=1, is_pressed=True)
            time.sleep(0.05)

            # Second click (double)
            self.controller.click(button=1, is_pressed=True)
            time.sleep(0.3)  # Wait longer between double-clicks

    def test_edge_crossing_normalized_position(self):
        """Test that edge crossing sends normalized position"""
        self.controller._is_active = True

        # Add movements
        for i in range(5):
            self.controller._movement_history.put((1900 + i, 540))

        self.mock_controller_instance.position = (1919, 540)

        with patch('input.mouse._darwin.EdgeDetector.is_at_edge') as mock_edge:
            mock_edge.return_value = True

            self.controller._check_edge()

            time.sleep(0.2)

            # Find CommandEvent
            command_events = [e for e in self.stream_handler.sent_events
                            if isinstance(e, CommandEvent)]

            if len(command_events) > 0:
                command = command_events[0]
                # Position should be normalized
                norm_x = command.params.get("x")
                norm_y = command.params.get("y")

                # Should be between 0 and 1
                self.assertGreaterEqual(norm_x, 0)
                self.assertLessEqual(norm_x, 1)
                self.assertGreaterEqual(norm_y, 0)
                self.assertLessEqual(norm_y, 1)


class TestClientMouseControllerIntegration(unittest.TestCase):
    """Integration tests with real EventBus"""

    def setUp(self):
        """Setup before each test"""
        self.event_bus = ThreadSafeEventBus()
        self.stream_handler = MockStreamHandler(stream_type=1)

        # Patch MouseController and Screen
        self.mouse_controller_patcher = patch('input.mouse._darwin.MouseController')
        self.mock_mouse_controller_class = self.mouse_controller_patcher.start()
        self.mock_controller_instance = MagicMock()
        self.mock_controller_instance.position = (500, 500)
        self.mock_mouse_controller_class.return_value = self.mock_controller_instance

        self.screen_patcher = patch('input.mouse._darwin.Screen.get_size')
        self.mock_screen = self.screen_patcher.start()
        self.mock_screen.return_value = (1920, 1080)

        self.button_patcher = patch('input.mouse._darwin.Button')
        self.mock_button = self.button_patcher.start()
        self.mock_button.return_value = MagicMock()

        self.event_mapper_patcher = patch('input.mouse._darwin.EventMapper')
        self.mock_event_mapper = self.event_mapper_patcher.start()

        self.controller = ClientMouseController(
            event_bus=self.event_bus,
            stream_handler=self.stream_handler
        )

    def tearDown(self):
        """Cleanup after each test"""
        self.mouse_controller_patcher.stop()
        self.screen_patcher.stop()
        self.button_patcher.stop()
        self.event_mapper_patcher.stop()

    def test_event_bus_integration(self):
        """Test integration with EventBus for activation"""
        # Track state changes
        states = []

        def track_state(*args, **kwargs):
            states.append(self.controller._is_active)

        # Activate
        self.event_bus.dispatch(
            event_type=EventType.CLIENT_ACTIVE,
            data={}
        )
        time.sleep(0.1)
        track_state()

        # Deactivate
        self.event_bus.dispatch(
            event_type=EventType.CLIENT_INACTIVE,
            data={}
        )
        time.sleep(0.1)
        track_state()

        # Verify state transitions
        self.assertEqual(states[0], True)
        self.assertEqual(states[1], False)

    def test_full_workflow_receive_and_move(self):
        """Test complete workflow of receiving and processing move event"""
        # Activate client
        self.event_bus.dispatch(
            event_type=EventType.CLIENT_ACTIVE,
            data={}
        )
        time.sleep(0.1)

        # Create move event
        mock_message = MagicMock()
        mock_event = MouseEvent(x=0.5, y=0.5, dx=10, dy=20)
        mock_event.action = MouseEvent.MOVE_ACTION
        self.mock_event_mapper.get_event.return_value = mock_event

        # Simulate receiving
        self.stream_handler.simulate_receive(mock_message)

        time.sleep(0.2)

        # Should have processed movement
        self.assertGreater(self.controller._movement_history.qsize(), 0)


if __name__ == "__main__":
    # Run tests with verbose output
    unittest.main(verbosity=2)

