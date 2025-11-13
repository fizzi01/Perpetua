"""
Terminal-based interactive test for ClientMouseController.
Run this to test client mouse control without a GUI.
"""
import time
import sys
from threading import Thread, Event as ThreadEvent

from pynput.mouse import Button, Controller

from event.EventBus import ThreadSafeEventBus
from event import EventType, MouseEvent, CommandEvent, EventMapper
from input.mouse import ClientMouseController
from network.stream.GenericStream import StreamHandler
from utils.logging import Logger


class TerminalStreamHandler(StreamHandler):
    """Stream handler that prints events to terminal"""

    def __init__(self, stream_type: int):
        self.stream_type = stream_type
        self.sent_events = []
        self._active = False
        self._receive_callback = None
        self._receive_callback_type = None

        self.move_count = 0
        self.click_count = 0
        self.scroll_count = 0
        self.cross_screen_count = 0

        self.logger = Logger.get_instance()

    def send(self, event):
        """Mock send method that logs events"""
        self.sent_events.append(event)
        timestamp = time.strftime("%H:%M:%S")

        if isinstance(event, CommandEvent):
            self.cross_screen_count += 1
            print(f"\n{'='*60}")
            print(f"⚠️  [{timestamp}] CROSS SCREEN EVENT DETECTED!")
            print(f"   Command: {event.command}")
            print(f"   Position: x={event.params.get('x'):.6f}, y={event.params.get('y'):.6f}")
            print(f"   Total cross-screen events: {self.cross_screen_count}")
            print(f"{'='*60}\n")

    def register_receive_callback(self, callback, message_type=None):
        """Register callback for receiving messages"""
        self._receive_callback = callback
        self._receive_callback_type = message_type

    def simulate_receive(self, event):
        """Simulate receiving an event from server"""
        if self._receive_callback:
            # Patch EventMapper temporarily
            import input.mouse as darwin_module
            original_mapper = EventMapper.get_event
            EventMapper.get_event = lambda msg: event

            try:
                mock_message = type('MockMessage', (), {'event': event, 'type': 'mouse'})()
                self._receive_callback(mock_message)
            finally:
                EventMapper.get_event = original_mapper

    def start(self):
        self._active = True

    def stop(self):
        self._active = False

    def print_stats(self):
        """Print current statistics"""
        print(f"\n{'='*60}")
        print(f"📊 STATISTICS")
        print(f"{'='*60}")
        print(f"   Move Events Sent: {self.move_count}")
        print(f"   Click Events Sent: {self.click_count}")
        print(f"   Scroll Events Sent: {self.scroll_count}")
        print(f"   Cross Screen Events: {self.cross_screen_count}")
        print(f"   Total Events: {len(self.sent_events)}")
        print(f"{'='*60}\n")


class TerminalClientMouseControllerTest:
    """Terminal-based interactive test"""

    def __init__(self):
        # Initialize logger
        Logger(stdout=print, logging=True)
        self.logger = Logger.get_instance()

        # Event bus
        self.event_bus = ThreadSafeEventBus()

        # Stream handler
        self.stream_handler = TerminalStreamHandler(stream_type=1)

        # Mouse controller
        self.mouse_controller = None
        self.is_active = False

        # Stop flag
        self.stop_flag = ThreadEvent()

        # Setup event bus
        self.event_bus.subscribe(EventType.CLIENT_ACTIVE, self._on_client_active)
        self.event_bus.subscribe(EventType.CLIENT_INACTIVE, self._on_client_inactive)

    def _on_client_active(self, data):
        """Handle client active event"""
        self.is_active = True
        print("\n✓ Client ACTIVATED")
        print("  Ready to receive mouse events from server\n")

    def _on_client_inactive(self, data):
        """Handle client inactive event"""
        self.is_active = False
        print("\n✓ Client DEACTIVATED")
        print("  No longer receiving mouse events\n")

    def init_controller(self):
        """Initialize the mouse controller"""
        print("\n🚀 Initializing ClientMouseController...")

        try:
            self.mouse_controller = ClientMouseController(
                event_bus=self.event_bus,
                stream_handler=self.stream_handler
            )
            print("✓ Mouse controller initialized successfully!\n")
            return True

        except Exception as e:
            print(f"✗ Failed to initialize controller: {e}")
            return False

    def activate_client(self):
        """Activate the client"""
        print("\n🔄 Activating client...")
        self.event_bus.dispatch(
            event_type=EventType.CLIENT_ACTIVE,
            data={}
        )
        time.sleep(0.1)

    def deactivate_client(self):
        """Deactivate the client"""
        print("\n🔄 Deactivating client...")
        self.event_bus.dispatch(
            event_type=EventType.CLIENT_INACTIVE,
            data={}
        )
        time.sleep(0.1)

    def send_move_event(self, x, y, dx=0, dy=0):
        """Send a move event"""
        event = MouseEvent(x=x, y=y, dx=dx, dy=dy)
        event.action = MouseEvent.MOVE_ACTION

        self.stream_handler.simulate_receive(event)
        self.stream_handler.move_count += 1

        if dx > 0 or dy > 0:
            print(f"📤 Sent REL MOVE: dx={dx}, dy={dy}")
        else:
            print(f"📤 Sent ABS MOVE: x={x:.3f}, y={y:.3f}")

    def send_click_event(self, button, is_pressed=True):
        """Send a click event"""
        event = MouseEvent(x=0, y=0, button=button)
        event.action = MouseEvent.CLICK_ACTION
        event.is_pressed = is_pressed

        self.stream_handler.simulate_receive(event)
        self.stream_handler.click_count += 1

        action = "PRESS" if is_pressed else "RELEASE"
        print(f"🖱️  Sent CLICK {action}: button={button}")

    def send_scroll_event(self, dx, dy):
        """Send a scroll event"""
        event = MouseEvent(x=dx, y=dy)
        event.action = MouseEvent.SCROLL_ACTION
        event.dx = dx
        event.dy = dy

        self.stream_handler.simulate_receive(event)
        self.stream_handler.scroll_count += 1

        print(f"📜 Sent SCROLL: dx={dx}, dy={dy}")

    def print_help(self):
        """Print help menu"""
        print(f"\n{'='*60}")
        print("COMMANDS")
        print(f"{'='*60}")
        print("Client Control:")
        print("  a - Activate client")
        print("  d - Deactivate client")
        print("\nSend Events:")
        print("  m - Send absolute move event (center)")
        print("  r - Send relative move event (+10, +10)")
        print("  c - Send click press")
        print("  u - Send click release")
        print("  s - Send scroll down")
        print("\nOther:")
        print("  t - Show statistics")
        print("  x - Clear statistics")
        print("  h - Show this help")
        print("  q - Quit")
        print(f"{'='*60}\n")

    def run(self):
        """Run the interactive test"""
        print("\n" + "="*60)
        print("ClientMouseController Terminal Test")
        print("="*60)
        print("\nThis test simulates a client receiving mouse events from the server.")
        print("Events will move your actual mouse cursor.")
        print("\nNote: You need Accessibility permissions for this to work on macOS.")
        print("="*60)

        # Initialize controller
        if not self.init_controller():
            return

        # Print help
        self.print_help()

        # Command loop
        while not self.stop_flag.is_set():
            try:
                cmd = input("Enter command (h for help): ").strip().lower()

                if cmd == 'q':
                    print("\n👋 Quitting...")
                    self.stop_flag.set()
                    break

                elif cmd == 'a':
                    self.activate_client()

                elif cmd == 'd':
                    self.deactivate_client()

                elif cmd == 'm':
                    if not self.is_active:
                        print("⚠️  Client is not active! Activate first with 'a'")
                    else:
                        self.send_move_event(x=0.5, y=0.5, dx=0, dy=0)

                elif cmd == 'r':
                    if not self.is_active:
                        print("⚠️  Client is not active! Activate first with 'a'")
                    else:
                        self.send_move_event(x=0, y=0, dx=10, dy=10)

                elif cmd == 'c':
                    if not self.is_active:
                        print("⚠️  Client is not active! Activate first with 'a'")
                    else:
                        self.send_click_event(button=Button.left, is_pressed=True)

                elif cmd == 'u':
                    if not self.is_active:
                        print("⚠️  Client is not active! Activate first with 'a'")
                    else:
                        self.send_click_event(button=Button.left, is_pressed=False)

                elif cmd == 's':
                    if not self.is_active:
                        print("⚠️  Client is not active! Activate first with 'a'")
                    else:
                        self.send_scroll_event(dx=0, dy=-5)

                elif cmd == 't':
                    self.stream_handler.print_stats()

                elif cmd == 'x':
                    self.stream_handler.move_count = 0
                    self.stream_handler.click_count = 0
                    self.stream_handler.scroll_count = 0
                    self.stream_handler.cross_screen_count = 0
                    self.stream_handler.sent_events = []
                    print("\n✓ Statistics cleared")

                elif cmd == 'h':
                    self.print_help()

                elif cmd:
                    print(f"Unknown command: {cmd}")
                    self.print_help()

            except EOFError:
                self.stop_flag.set()
                break
            except KeyboardInterrupt:
                print("\n\n👋 Interrupted by user")
                self.stop_flag.set()
                break

        # Cleanup
        self.stream_handler.print_stats()
        print("\n✓ Test completed\n")


def main():
    """Main entry point"""
    test = TerminalClientMouseControllerTest()
    test.run()


if __name__ == "__main__":
    main()

