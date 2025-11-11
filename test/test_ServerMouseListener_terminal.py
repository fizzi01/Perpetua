"""
Simple terminal-based interactive test for ServerMouseListener edge crossing.
Run this to test edge detection without a GUI.
"""
import time
import sys
from threading import Thread, Event as ThreadEvent

from event.EventBus import ThreadSafeEventBus
from event.Event import EventType, MouseEvent
from input.mouse._darwin import ServerMouseListener
from network.stream.GenericStream import StreamHandler
from utils.logging.logger import Logger


class TerminalStreamHandler(StreamHandler):
    """Stream handler that prints events to terminal"""

    def __init__(self, stream_type: int):
        self.stream_type = stream_type
        self.sent_events = []
        self._active = False
        self.edge_count = 0
        self.click_count = 0
        self.scroll_count = 0
        self.logger = Logger.get_instance()

    def send(self, event):
        """Mock send method that logs events"""
        self.sent_events.append(event)
        timestamp = time.strftime("%H:%M:%S")

        if isinstance(event, MouseEvent):
            if event.action == "move":
                # Edge crossing
                self.edge_count += 1
                edge = "UNKNOWN"
                if event.x == 0:
                    edge = "LEFT"
                elif event.x == 1:
                    edge = "RIGHT"
                elif event.y == 0:
                    edge = "TOP"
                elif event.y == 1:
                    edge = "BOTTOM"

                print(f"\n{'='*60}")
                print(f"🎯 [{timestamp}] EDGE CROSSING DETECTED!")
                print(f"   Edge: {edge}")
                print(f"   Position: x={event.x:.6f}, y={event.y:.6f}")
                print(f"   Total crossings: {self.edge_count}")
                print(f"{'='*60}\n")

            elif event.action in ["press", "release"]:
                self.click_count += 1
                print(f"🖱️  [{timestamp}] CLICK: button={event.button}, "
                      f"{event.action} at ({event.x}, {event.y}) - Total: {self.click_count}")

            elif event.action == "scroll":
                self.scroll_count += 1
                print(f"📜 [{timestamp}] SCROLL: dx={event.x}, dy={event.y} - Total: {self.scroll_count}")

    def start(self):
        self._active = True

    def stop(self):
        self._active = False

    def print_stats(self):
        """Print current statistics"""
        print(f"\n{'='*60}")
        print(f"STATISTICS")
        print(f"{'='*60}")
        print(f"   Edge Crossings: {self.edge_count}")
        print(f"   Clicks: {self.click_count}")
        print(f"   Scrolls: {self.scroll_count}")
        print(f"   Total Events: {len(self.sent_events)}")
        print(f"{'='*60}\n")


class TerminalEdgeCrossingTest:
    """Terminal-based interactive test"""

    def __init__(self):
        # Initialize logger
        Logger(stdout=print, logging=True)
        self.logger = Logger.get_instance()

        # Event bus
        self.event_bus = ThreadSafeEventBus()

        # Stream handler
        self.stream_handler = TerminalStreamHandler(stream_type=1)

        # Mouse listener
        self.mouse_listener = None
        self.listener_active = False

        # Current mode
        self.listening_mode = False

        # Stop flag
        self.stop_flag = ThreadEvent()

        # Setup event bus
        self.event_bus.subscribe(EventType.ACTIVE_SCREEN_CHANGED, self._on_active_screen_changed)

    def _on_active_screen_changed(self, data):
        """Handle active screen changed event"""
        active_screen = data.get("active_screen")

        if active_screen:
            self.listening_mode = True
            print(f"\n✓ Active screen changed to: {active_screen}")
            print("  Mode: LISTENING (capturing clicks and scrolls)")
        else:
            self.listening_mode = False
            print("\n✓ Active screen changed to: None")
            print("  Mode: EDGE DETECTION")

    def start_listener(self):
        """Start the mouse listener"""
        if not self.listener_active:
            print("\n🚀 Starting ServerMouseListener...")

            try:
                self.mouse_listener = ServerMouseListener(
                    event_bus=self.event_bus,
                    stream_handler=self.stream_handler,
                    filtering=True
                )
                self.mouse_listener.start()
                self.listener_active = True

                print("✓ Mouse listener started successfully!")
                print("\n📍 Current mode: EDGE DETECTION")
                print("   Move your cursor to screen edges to trigger detection\n")

            except Exception as e:
                print(f"✗ Failed to start listener: {e}")
                return False

        return True

    def stop_listener(self):
        """Stop the mouse listener"""
        if self.listener_active and self.mouse_listener:
            print("\n🛑 Stopping mouse listener...")

            try:
                self.mouse_listener.stop()
                self.listener_active = False
                print("✓ Mouse listener stopped")

            except Exception as e:
                print(f"✗ Failed to stop listener: {e}")

    def toggle_mode(self):
        """Toggle between edge detection and listening mode"""
        if not self.listening_mode:
            print("\n🔄 Switching to LISTENING mode...")
            self.event_bus.dispatch(
                event_type=EventType.ACTIVE_SCREEN_CHANGED,
                data={"active_screen": "right"}
            )
        else:
            print("\n🔄 Switching to EDGE DETECTION mode...")
            self.event_bus.dispatch(
                event_type=EventType.ACTIVE_SCREEN_CHANGED,
                data={"active_screen": None}
            )

    def print_help(self):
        """Print help menu"""
        print(f"\n{'='*60}")
        print("COMMANDS")
        print(f"{'='*60}")
        print("  s - Show statistics")
        print("  m - Toggle mode (Edge Detection ⟷ Listening)")
        print("  c - Clear statistics")
        print("  h - Show this help")
        print("  q - Quit")
        print(f"{'='*60}\n")

    def run(self):
        """Run the interactive test"""
        print("\n" + "="*60)
        print("ServerMouseListener Edge Crossing Terminal Test")
        print("="*60)
        print("\nThis test uses the actual mouse cursor to test edge detection.")
        print("\nNote: You need Accessibility permissions for this to work on macOS.")
        print("="*60)

        # Start listener
        if not self.start_listener():
            return

        # Print help
        self.print_help()

        # Start command input thread
        def command_loop():
            while not self.stop_flag.is_set():
                try:
                    cmd = input("Enter command (h for help): ").strip().lower()

                    if cmd == 'q':
                        print("\n👋 Quitting...")
                        self.stop_flag.set()
                        break
                    elif cmd == 's':
                        self.stream_handler.print_stats()
                    elif cmd == 'm':
                        self.toggle_mode()
                    elif cmd == 'c':
                        self.stream_handler.edge_count = 0
                        self.stream_handler.click_count = 0
                        self.stream_handler.scroll_count = 0
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

        # Run command loop
        try:
            command_loop()
        finally:
            # Cleanup
            self.stop_listener()
            self.stream_handler.print_stats()
            print("\n✓ Test completed\n")


def main():
    """Main entry point"""
    test = TerminalEdgeCrossingTest()
    test.run()


if __name__ == "__main__":
    main()

