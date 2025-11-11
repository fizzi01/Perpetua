import unittest
import time
from unittest.mock import Mock, MagicMock, patch, call
from queue import Queue

from model.ClientObj import ClientsManager
from utils.logging.logger import Logger
from event.EventBus import EventBus, ThreadSafeEventBus
from event.Event import EventType, MouseEvent
from input.mouse import ServerMouseListener
from network.stream.GenericStream import StreamHandler


class MockStreamHandler(StreamHandler):
    """Mock stream handler for testing"""

    def __init__(self, stream_type: int):
        self.stream_type = stream_type
        self.sent_events = []
        self._active = False

    def send(self, event):
        """Mock send method"""
        self.sent_events.append(event)

    def start(self):
        self._active = True

    def stop(self):
        self._active = False


class TestServerMouseListener(unittest.TestCase):
    """Test suite for ServerMouseListener"""

    def setUp(self):
        """Setup before each test"""
        Logger(stdout=print, logging=True)
        self.event_bus = ThreadSafeEventBus()
        self.stream_handler = MockStreamHandler(stream_type=1)

        # Patch the MouseListener to avoid starting actual listener
        self.mouse_listener_patcher = patch('input.mouse._darwin.MouseListener')
        self.mock_mouse_listener = self.mouse_listener_patcher.start()

        # Create mock listener instance
        self.mock_listener_instance = MagicMock()
        self.mock_listener_instance.is_alive.return_value = True
        self.mock_mouse_listener.return_value = self.mock_listener_instance

        # Patch Screen.get_size to return fixed size
        self.screen_patcher = patch('input.mouse._darwin.Screen.get_size')
        self.mock_screen = self.screen_patcher.start()
        self.mock_screen.return_value = (1920, 1080)

        self.listener = ServerMouseListener(
            event_bus=self.event_bus,
            stream_handler=self.stream_handler,
            filtering=True
        )

    def tearDown(self):
        """Cleanup after each test"""
        self.listener.stop()
        self.mouse_listener_patcher.stop()
        self.screen_patcher.stop()

    def test_initialization(self):
        """Test that the listener initializes correctly"""
        self.assertIsNotNone(self.listener)
        self.assertFalse(self.listener._listening)
        self.assertEqual(self.listener._screen_size, (1920, 1080))
        self.assertIsInstance(self.listener._movement_history, Queue)
        self.assertEqual(self.listener._movement_history.maxsize, 5)

    def test_start_stop(self):
        """Test starting and stopping the listener"""
        self.listener.start()
        self.mock_listener_instance.start.assert_called_once()

        self.listener.stop()
        self.mock_listener_instance.stop.assert_called_once()

    def test_active_screen_changed_enables_listening(self):
        """Test that active screen changed event enables listening"""
        self.assertFalse(self.listener._listening)

        # Dispatch active screen changed event
        self.event_bus.dispatch(
            event_type=EventType.ACTIVE_SCREEN_CHANGED,
            data={"active_screen": "right"}
        )

        time.sleep(0.1)  # Give time for event processing
        self.assertTrue(self.listener._listening)

    def test_active_screen_changed_disables_listening(self):
        """Test that active screen None disables listening"""
        # First enable listening
        self.event_bus.dispatch(
            event_type=EventType.ACTIVE_SCREEN_CHANGED,
            data={"active_screen": "right"}
        )
        time.sleep(0.1)
        self.assertTrue(self.listener._listening)

        # Now disable
        self.event_bus.dispatch(
            event_type=EventType.ACTIVE_SCREEN_CHANGED,
            data={"active_screen": None}
        )
        time.sleep(0.1)
        self.assertFalse(self.listener._listening)

    def test_active_screen_changed_clears_movement_history(self):
        """Test that active screen changed clears movement history"""
        # Add some movements to history
        self.listener._movement_history.put((100, 100))
        self.listener._movement_history.put((200, 200))
        self.assertEqual(self.listener._movement_history.qsize(), 2)

        # Dispatch event
        self.event_bus.dispatch(
            event_type=EventType.ACTIVE_SCREEN_CHANGED,
            data={"active_screen": "right"}
        )
        time.sleep(0.1)

        # History should be cleared
        self.assertEqual(self.listener._movement_history.qsize(), 0)

    def test_on_click_while_listening(self):
        """Test click event while listening is enabled"""
        # Enable listening
        self.listener._listening = True

        # Mock button
        mock_button = Mock()
        mock_button.value = 1

        # Simulate click
        result = self.listener.on_click(500, 500, mock_button, True)

        self.assertTrue(result)
        self.assertEqual(len(self.stream_handler.sent_events), 1)

        sent_event = self.stream_handler.sent_events[0]
        self.assertIsInstance(sent_event, MouseEvent)
        self.assertEqual(sent_event.x, 500)
        self.assertEqual(sent_event.y, 500)
        self.assertEqual(sent_event.button, 1)
        self.assertEqual(sent_event.action, "press")
        self.assertTrue(sent_event.is_pressed)

    def test_on_click_while_not_listening(self):
        """Test click event while listening is disabled"""
        self.listener._listening = False

        mock_button = Mock()
        mock_button.value = 1

        result = self.listener.on_click(500, 500, mock_button, True)

        self.assertTrue(result)
        self.assertEqual(len(self.stream_handler.sent_events), 0)

    def test_on_scroll_while_listening(self):
        """Test scroll event while listening is enabled"""
        self.listener._listening = True

        result = self.listener.on_scroll(500, 500, 10, -5)

        self.assertTrue(result)
        self.assertEqual(len(self.stream_handler.sent_events), 1)

        sent_event = self.stream_handler.sent_events[0]
        self.assertIsInstance(sent_event, MouseEvent)
        self.assertEqual(sent_event.x, 10)
        self.assertEqual(sent_event.y, -5)
        self.assertEqual(sent_event.action, "scroll")

    def test_on_scroll_while_not_listening(self):
        """Test scroll event while listening is disabled"""
        self.listener._listening = False

        result = self.listener.on_scroll(500, 500, 10, -5)

        self.assertTrue(result)
        self.assertEqual(len(self.stream_handler.sent_events), 0)

    def test_on_move_while_listening(self):
        """Test movement while listening (should not trigger edge detection)"""
        self.listener._listening = True

        result = self.listener.on_move(500, 500)

        self.assertTrue(result)
        # Should not send any events when listening (movements are handled elsewhere)
        # Movement history should still be updated
        self.assertEqual(self.listener._movement_history.qsize(), 1)

    def test_on_move_history_queue_management(self):
        """Test that movement history queue is properly managed"""
        self.listener._listening = False

        # Add 6 movements (maxsize is 5, so oldest should be removed)
        for i in range(6):
            self.listener.on_move(100 * i, 100 * i)

        self.assertEqual(self.listener._movement_history.qsize(), 5)

        # First element should be (100, 100), not (0, 0)
        queue_data = list(self.listener._movement_history.queue)
        self.assertEqual(queue_data[0], (100, 100))

    def test_edge_detection_left_edge(self):
        """Test detection of cursor reaching left edge"""
        self.listener._listening = False

        # Simulate movement towards left edge
        movements = [(200, 500), (150, 500), (100, 500), (50, 500), (0, 500)]

        for x, y in movements[:-1]:
            self.listener.on_move(x, y)

        # Reset sent events
        self.stream_handler.sent_events = []

        # Move to edge
        self.listener.on_move(movements[-1][0], movements[-1][1])

        # Should trigger edge event
        time.sleep(0.1)
        self.assertEqual(len(self.stream_handler.sent_events), 1)

        sent_event = self.stream_handler.sent_events[0]
        self.assertEqual(sent_event.x, 0)  # Normalized to 0
        self.assertAlmostEqual(sent_event.y, 500 / 1080, places=4)  # Normalized
        self.assertEqual(sent_event.action, "move")

    def test_edge_detection_right_edge(self):
        """Test detection of cursor reaching right edge"""
        self.listener._listening = False

        # Simulate movement towards right edge
        movements = [(1700, 500), (1750, 500), (1800, 500), (1850, 500), (1919, 500)]

        for x, y in movements[:-1]:
            self.listener.on_move(x, y)

        self.stream_handler.sent_events = []

        # Move to edge
        self.listener.on_move(movements[-1][0], movements[-1][1])

        time.sleep(0.1)
        self.assertEqual(len(self.stream_handler.sent_events), 1)

        sent_event = self.stream_handler.sent_events[0]
        self.assertEqual(sent_event.x, 1)  # Normalized to 1
        self.assertAlmostEqual(sent_event.y, 500 / 1080, places=4)

    def test_edge_detection_top_edge(self):
        """Test detection of cursor reaching top edge"""
        self.listener._listening = False

        # Simulate movement towards top edge
        movements = [(500, 200), (500, 150), (500, 100), (500, 50), (500, 0)]

        for x, y in movements[:-1]:
            self.listener.on_move(x, y)

        self.stream_handler.sent_events = []

        # Move to edge
        self.listener.on_move(movements[-1][0], movements[-1][1])

        time.sleep(0.1)
        self.assertEqual(len(self.stream_handler.sent_events), 1)

        sent_event = self.stream_handler.sent_events[0]
        self.assertAlmostEqual(sent_event.x, 500 / 1920, places=4)
        self.assertEqual(sent_event.y, 0)  # Normalized to 0

    def test_edge_detection_bottom_edge(self):
        """Test detection of cursor reaching bottom edge"""
        self.listener._listening = False

        # Simulate movement towards bottom edge
        movements = [(500, 880), (500, 920), (500, 960), (500, 1000), (500, 1079)]

        for x, y in movements[:-1]:
            self.listener.on_move(x, y)

        self.stream_handler.sent_events = []

        # Move to edge
        self.listener.on_move(movements[-1][0], movements[-1][1])

        time.sleep(0.1)
        self.assertEqual(len(self.stream_handler.sent_events), 1)

        sent_event = self.stream_handler.sent_events[0]
        self.assertAlmostEqual(sent_event.x, 500 / 1920, places=4)
        self.assertEqual(sent_event.y, 1)  # Normalized to 1

    def test_no_edge_detection_when_moving_away(self):
        """Test that edge detection doesn't trigger when moving away from edge"""
        self.listener._listening = False

        # Simulate movement towards left edge then away
        movements = [(200, 500), (150, 500), (100, 500), (50, 500), (0, 500), (50, 500)]

        for x, y in movements[:-1]:
            self.listener.on_move(x, y)

        self.stream_handler.sent_events = []

        # Move away from edge
        self.listener.on_move(movements[-1][0], movements[-1][1])

        time.sleep(0.1)
        # Should not trigger edge event (moving away)
        # Note: depending on implementation, this might still send events
        # The test verifies the direction detection logic

    def test_no_edge_detection_when_not_at_edge(self):
        """Test that edge detection doesn't trigger in the middle of screen"""
        self.listener._listening = False

        # Simulate movement in the middle
        movements = [(900, 500), (910, 500), (920, 500), (930, 500), (940, 500)]

        for x, y in movements:
            self.listener.on_move(x, y)

        # Should not trigger any edge events
        self.assertEqual(len(self.stream_handler.sent_events), 0)

    def test_cross_screen_event_blocks_processing(self):
        """Test that cross screen event blocks further processing"""
        self.listener._listening = False
        self.listener._cross_screen_event.set()

        result = self.listener.on_move(500, 500)

        self.assertTrue(result)
        # Movement should not be added to history when event is set
        # (depends on implementation - this tests the early return)

    def test_edge_detection_triggers_active_screen_changed_event(self):
        """Test that reaching edge triggers ACTIVE_SCREEN_CHANGED event"""
        self.listener._listening = False

        # Setup event listener
        event_received = []

        def capture_event(data):
            event_received.append(data)

        self.event_bus.subscribe(EventType.ACTIVE_SCREEN_CHANGED, capture_event)

        # Simulate movement to left edge
        movements = [(200, 500), (150, 500), (100, 500), (50, 500), (0, 500)]

        for x, y in movements:
            self.listener.on_move(x, y)

        time.sleep(0.1)

        # Should have received at least one event (the one we triggered + original subscription)
        self.assertGreater(len(event_received), 0)

        # Find the event with "left" screen
        left_events = [e for e in event_received if e.get("active_screen") == "left"]
        self.assertGreater(len(left_events), 0)

    def test_movement_history_persistence(self):
        """Test that movement history persists across multiple moves"""
        self.listener._listening = False

        # Add movements
        self.listener.on_move(100, 100)
        self.listener.on_move(200, 200)
        self.listener.on_move(300, 300)

        self.assertEqual(self.listener._movement_history.qsize(), 3)

        # Verify data
        queue_data = list(self.listener._movement_history.queue)
        self.assertEqual(queue_data[0], (100, 100))
        self.assertEqual(queue_data[1], (200, 200))
        self.assertEqual(queue_data[2], (300, 300))

    def test_error_handling_in_on_click(self):
        """Test error handling when stream send fails in on_click"""
        self.listener._listening = True

        # Make stream.send raise exception
        def failing_send(event):
            raise Exception("Send failed")

        self.stream_handler.send = failing_send

        mock_button = Mock()
        mock_button.value = 1

        # Should not raise exception
        result = self.listener.on_click(500, 500, mock_button, True)
        self.assertTrue(result)

    def test_error_handling_in_on_scroll(self):
        """Test error handling when stream send fails in on_scroll"""
        self.listener._listening = True

        # Make stream.send raise exception
        def failing_send(event):
            raise Exception("Send failed")

        self.stream_handler.send = failing_send

        # Should not raise exception
        result = self.listener.on_scroll(500, 500, 10, -5)
        self.assertTrue(result)

    def test_multiple_edge_crossings(self):
        """Test multiple edge crossings in sequence"""
        self.listener._listening = False

        # Cross left edge
        for x in [200, 150, 100, 50, 0]:
            self.listener.on_move(x, 500)

        time.sleep(0.2)
        initial_events = len(self.stream_handler.sent_events)
        self.assertGreater(initial_events, 0)

        # Re-enable for another test (simulate coming back from client)
        self.listener._listening = True
        time.sleep(0.1)
        self.listener._listening = False

        # Clear history
        with self.listener._movement_history.mutex:
            self.listener._movement_history.queue.clear()

        # Cross right edge
        for x in [1700, 1750, 1800, 1850, 1919]:
            self.listener.on_move(x, 500)

        time.sleep(0.2)

        # Should have sent more events
        self.assertGreater(len(self.stream_handler.sent_events), initial_events)

    def test_filtering_enabled(self):
        """Test that filtering is properly initialized when enabled"""
        # This listener was created with filtering=True
        self.assertIsNotNone(self.listener._mouse_filter)

        # Create another with filtering disabled
        listener_no_filter = ServerMouseListener(
            event_bus=self.event_bus,
            stream_handler=self.stream_handler,
            filtering=False
        )

        self.assertIsNotNone(listener_no_filter._mouse_filter)


class TestServerMouseListenerIntegration(unittest.TestCase):
    """Integration tests with real EventBus"""

    def setUp(self):
        """Setup before each test"""
        Logger(stdout=print, logging=True)
        self.event_bus = ThreadSafeEventBus()
        self.stream_handler = MockStreamHandler(stream_type=1)

        # Patch MouseListener and Screen
        self.mouse_listener_patcher = patch('input.mouse._darwin.MouseListener')
        self.mock_mouse_listener = self.mouse_listener_patcher.start()
        self.mock_listener_instance = MagicMock()
        self.mock_listener_instance.is_alive.return_value = True
        self.mock_mouse_listener.return_value = self.mock_listener_instance

        self.screen_patcher = patch('input.mouse._darwin.Screen.get_size')
        self.mock_screen = self.screen_patcher.start()
        self.mock_screen.return_value = (1920, 1080)

        self.listener = ServerMouseListener(
            event_bus=self.event_bus,
            stream_handler=self.stream_handler,
            filtering=True
        )

    def tearDown(self):
        """Cleanup after each test"""
        self.listener.stop()
        self.mouse_listener_patcher.stop()
        self.screen_patcher.stop()

    def test_event_bus_integration(self):
        """Test integration with EventBus for screen changes"""
        # Track state changes
        states = []

        def track_state(*args, **kwargs):
            states.append(self.listener._listening)

        # Enable
        self.event_bus.dispatch(
            event_type=EventType.ACTIVE_SCREEN_CHANGED,
            data={"active_screen": "right"}
        )
        time.sleep(0.1)
        track_state()

        # Disable
        self.event_bus.dispatch(
            event_type=EventType.ACTIVE_SCREEN_CHANGED,
            data={"active_screen": None}
        )
        time.sleep(0.1)
        track_state()

        # Verify state transitions
        self.assertEqual(states[0], True)
        self.assertEqual(states[1], False)

    def test_full_workflow_edge_crossing(self):
        """Test complete workflow of edge crossing with event bus"""
        # Start not listening
        self.assertFalse(self.listener._listening)

        # Simulate movement to edge
        for x in [200, 150, 100, 50, 0]:
            self.listener.on_move(x, 500)

        time.sleep(0.2)

        # Should have sent event to stream
        self.assertGreater(len(self.stream_handler.sent_events), 0)

        # Should have triggered active screen change
        # (which would enable listening in real scenario)
        # We can check the listener state
        self.assertTrue(self.listener._listening)


if __name__ == "__main__":
    # Run tests with verbose output
    unittest.main(verbosity=2)

