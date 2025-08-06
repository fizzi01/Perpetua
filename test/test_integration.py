#!/usr/bin/env python3
"""
Comprehensive Server-Client Integration Test
Tests the full PyContinuity protocol with real server-client communication,
benchmarks performance under load, and validates the complete data flow.
"""

import asyncio
import json
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty
from typing import List, Dict, Any, Tuple
import tempfile
import random
import string

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from network.IOManager import MessageService, BaseMessageQueueManager
    from server.connections.ClientHandler import ClientHandler, ClientHandlerFactory
    from utils.protocol.message import MessageBuilder, ProtocolMessage
    from utils.protocol.adapter import ProtocolAdapter
    from utils.net.ChunkManager import ChunkManager
    from utils.Logging import Logger
    IMPORTS_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Some imports not available: {e}")
    IMPORTS_AVAILABLE = False


class TestServer:
    """Mock server for testing."""
    
    def __init__(self, host='localhost', port=0):
        self.host = host
        self.port = port
        self.server_socket = None
        self.running = False
        self.clients = []
        self.message_queue = Queue()
        self.processed_messages = []
        self.message_count = 0
        self.start_time = None
        
    def start(self):
        """Start the test server."""
        if not IMPORTS_AVAILABLE:
            print("Skipping server start - dependencies not available")
            return 8080
            
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.port = self.server_socket.getsockname()[1]  # Get actual port
        self.server_socket.listen(5)
        self.running = True
        
        # Start acceptance thread
        threading.Thread(target=self._accept_connections, daemon=True).start()
        # Start message processing thread  
        threading.Thread(target=self._process_messages, daemon=True).start()
        
        return self.port
        
    def stop(self):
        """Stop the test server."""
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        for client in self.clients:
            client.stop()
            
    def _accept_connections(self):
        """Accept incoming client connections."""
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                # Wrap in our socket interface
                from network.ServerSocket import ServerSocket as ServerSocketWrapper
                wrapped_conn = ServerSocketWrapper(conn, addr)
                
                # Create client handler
                handler = ClientHandler(
                    client_socket=wrapped_conn,
                    screen="test_screen", 
                    command_processor=self._process_command
                )
                handler.start()
                self.clients.append(handler)
                
            except OSError:
                break
                
    def _process_command(self, command, screen):
        """Process commands from clients."""
        self.message_queue.put((command, screen, time.time()))
        
    def _process_messages(self):
        """Process messages from the queue."""
        while self.running:
            try:
                message, screen, timestamp = self.message_queue.get(timeout=0.1)
                self.processed_messages.append((message, screen, timestamp))
                self.message_count += 1
                
                if self.start_time is None:
                    self.start_time = timestamp
                    
            except Empty:
                continue


class TestClient:
    """Mock client for testing."""
    
    def __init__(self, server_host, server_port):
        self.server_host = server_host
        self.server_port = server_port
        self.socket = None
        self.message_service = None
        self.connected = False
        self.sent_messages = []
        
    def connect(self):
        """Connect to the test server."""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect((self.server_host, self.server_port))
        
        # Wrap in our client socket interface
        from network.ClientSocket import ClientSocket as ClientSocketWrapper
        wrapped_socket = ClientSocketWrapper(self.socket)
        
        # Create message service
        self.message_service = MessageService(
            socket_service=wrapped_socket,
            batch_interval=0.01,
            max_batch_size=10
        )
        self.message_service.start()
        self.connected = True
        
    def disconnect(self):
        """Disconnect from server."""
        if self.message_service:
            self.message_service.stop()
        if self.socket:
            self.socket.close()
        self.connected = False
        
    def send_mouse_event(self, x: float, y: float, event: str = "move"):
        """Send a mouse event."""
        if not self.connected:
            return False
            
        try:
            # Create mouse message using new protocol
            builder = MessageBuilder()
            message = builder.create_mouse_message(x, y, event)
            self.message_service.send_message("mouse", message, "test_screen")
            self.sent_messages.append(("mouse", x, y, event))
            return True
        except Exception as e:
            print(f"Error sending mouse event: {e}")
            return False
            
    def send_keyboard_event(self, key: str, event: str = "press"):
        """Send a keyboard event."""
        if not self.connected:
            return False
            
        try:
            builder = MessageBuilder()
            message = builder.create_keyboard_message(key, event)
            self.message_service.send_message("keyboard", message, "test_screen")
            self.sent_messages.append(("keyboard", key, event))
            return True
        except Exception as e:
            print(f"Error sending keyboard event: {e}")
            return False
            
    def send_clipboard_data(self, content: str, data_type: str = "text"):
        """Send clipboard data."""
        if not self.connected:
            return False
            
        try:
            builder = MessageBuilder()
            message = builder.create_clipboard_message(content, data_type)
            self.message_service.send_message("clipboard", message, "test_screen")
            self.sent_messages.append(("clipboard", content, data_type))
            return True
        except Exception as e:
            print(f"Error sending clipboard data: {e}")
            return False
            
    def send_file(self, file_path: str, file_data: bytes):
        """Send a file using the new protocol."""
        if not self.connected:
            return False
            
        try:
            file_name = os.path.basename(file_path)
            file_size = len(file_data)
            
            # Use the new file transfer method
            self.message_service._send_file(file_name, file_data, "test_screen")
            self.sent_messages.append(("file", file_name, file_size))
            return True
        except Exception as e:
            print(f"Error sending file: {e}")
            return False


class IntegrationTestSuite:
    """Main test suite for server-client integration."""
    
    def __init__(self):
        self.server = None
        self.clients = []
        self.results = {}
        
    def setup(self):
        """Set up the test environment."""
        print("Setting up test environment...")
        
        # Start test server
        self.server = TestServer()
        port = self.server.start()
        print(f"Test server started on port {port}")
        
        # Give server time to start
        time.sleep(0.1)
        return port
        
    def teardown(self):
        """Clean up test environment."""
        print("Cleaning up test environment...")
        
        # Disconnect all clients
        for client in self.clients:
            client.disconnect()
        self.clients.clear()
        
        # Stop server
        if self.server:
            self.server.stop()
            
    def test_basic_connectivity(self, port):
        """Test basic server-client connectivity."""
        print("\n=== Test 1: Basic Connectivity ===")
        
        try:
            client = TestClient('localhost', port)
            client.connect()
            self.clients.append(client)
            
            # Wait for connection to establish
            time.sleep(0.1)
            
            if len(self.server.clients) > 0:
                print("✓ Client connected successfully")
                return True
            else:
                print("✗ Client connection failed")
                return False
                
        except Exception as e:
            print(f"✗ Connection test failed: {e}")
            return False
            
    def test_message_types(self, port):
        """Test all message types."""
        print("\n=== Test 2: All Message Types ===")
        
        try:
            client = TestClient('localhost', port)
            client.connect()
            self.clients.append(client)
            
            # Test each message type
            test_results = []
            
            # Mouse events
            result = client.send_mouse_event(100.5, 200.7, "move")
            test_results.append(("mouse", result))
            
            result = client.send_mouse_event(150.3, 250.9, "click")
            test_results.append(("mouse_click", result))
            
            # Keyboard events
            result = client.send_keyboard_event("a", "press")
            test_results.append(("keyboard", result))
            
            result = client.send_keyboard_event("shift", "release")
            test_results.append(("keyboard_release", result))
            
            # Clipboard
            result = client.send_clipboard_data("Hello, World!", "text")
            test_results.append(("clipboard", result))
            
            # File transfer
            test_file_data = b"Test file content for integration testing"
            result = client.send_file("test_file.txt", test_file_data)
            test_results.append(("file", result))
            
            # Wait for processing
            time.sleep(0.5)
            
            # Check results
            success_count = sum(1 for _, success in test_results if success)
            total_count = len(test_results)
            
            print(f"Sent {total_count} different message types")
            print(f"Successful sends: {success_count}/{total_count}")
            
            if success_count == total_count:
                print("✓ All message types sent successfully")
                return True
            else:
                print(f"✗ Some message types failed: {total_count - success_count} failures")
                return False
                
        except Exception as e:
            print(f"✗ Message types test failed: {e}")
            return False
            
    def test_chunking_large_messages(self, port):
        """Test chunking with large messages."""
        print("\n=== Test 3: Large Message Chunking ===")
        
        try:
            client = TestClient('localhost', port)
            client.connect()
            self.clients.append(client)
            
            # Create large data that will require chunking
            large_data = "x" * 50000  # 50KB of data
            
            # Send large clipboard data
            success = client.send_clipboard_data(large_data, "text")
            
            if success:
                print(f"✓ Large message ({len(large_data)} bytes) sent successfully")
                
                # Wait for processing
                time.sleep(1.0)
                
                # Check if message was received
                if self.server.message_count > 0:
                    print("✓ Large message processed by server")
                    return True
                else:
                    print("✗ Large message not processed by server")
                    return False
            else:
                print("✗ Failed to send large message")
                return False
                
        except Exception as e:
            print(f"✗ Large message test failed: {e}")
            return False
            
    def test_concurrent_clients(self, port, num_clients=5):
        """Test multiple concurrent clients."""
        print(f"\n=== Test 4: Concurrent Clients ({num_clients} clients) ===")
        
        try:
            # Create multiple clients
            test_clients = []
            for i in range(num_clients):
                client = TestClient('localhost', port)
                client.connect()
                test_clients.append(client)
                self.clients.append(client)
                
            print(f"Connected {len(test_clients)} clients")
            
            # Send messages from all clients simultaneously
            def send_messages_from_client(client, client_id):
                messages_sent = 0
                try:
                    for i in range(10):
                        # Send different types of messages
                        client.send_mouse_event(client_id * 100 + i, client_id * 100 + i + 1)
                        client.send_keyboard_event(f"key_{i}", "press")
                        messages_sent += 2
                        time.sleep(0.01)  # Small delay between messages
                    return messages_sent
                except Exception as e:
                    print(f"Error in client {client_id}: {e}")
                    return 0
                    
            # Run clients concurrently
            with ThreadPoolExecutor(max_workers=num_clients) as executor:
                futures = []
                for i, client in enumerate(test_clients):
                    future = executor.submit(send_messages_from_client, client, i)
                    futures.append(future)
                    
                total_sent = sum(future.result() for future in as_completed(futures))
                
            print(f"Total messages sent: {total_sent}")
            
            # Wait for processing
            time.sleep(2.0)
            
            print(f"Server processed: {self.server.message_count} messages")
            
            # Check if most messages were processed (allow for some loss in testing)
            success_rate = self.server.message_count / total_sent if total_sent > 0 else 0
            
            if success_rate >= 0.8:  # 80% success rate acceptable
                print(f"✓ Concurrent client test passed ({success_rate:.1%} success rate)")
                return True
            else:
                print(f"✗ Concurrent client test failed ({success_rate:.1%} success rate)")
                return False
                
        except Exception as e:
            print(f"✗ Concurrent clients test failed: {e}")
            return False
            
    def test_performance_benchmark(self, port):
        """Benchmark performance under full load."""
        print("\n=== Test 5: Performance Benchmark ===")
        
        try:
            # Create single high-performance client
            client = TestClient('localhost', port)
            client.connect()
            self.clients.append(client)
            
            # Benchmark parameters
            num_messages = 1000
            message_types = ["mouse", "keyboard", "clipboard"]
            
            print(f"Sending {num_messages} messages as fast as possible...")
            
            start_time = time.time()
            self.server.message_count = 0  # Reset counter
            
            # Send messages rapidly
            for i in range(num_messages):
                msg_type = message_types[i % len(message_types)]
                
                if msg_type == "mouse":
                    client.send_mouse_event(i % 1920, (i * 2) % 1080, "move")
                elif msg_type == "keyboard":
                    key = chr(ord('a') + (i % 26))
                    client.send_keyboard_event(key, "press")
                elif msg_type == "clipboard":
                    content = f"clipboard_data_{i}"
                    client.send_clipboard_data(content, "text")
                    
            send_time = time.time() - start_time
            
            # Wait for processing
            time.sleep(2.0)
            
            total_time = time.time() - start_time
            processed_messages = self.server.message_count
            
            # Calculate metrics
            send_rate = num_messages / send_time
            process_rate = processed_messages / total_time
            success_rate = processed_messages / num_messages
            
            print(f"Send rate: {send_rate:.0f} messages/second")
            print(f"Processing rate: {process_rate:.0f} messages/second") 
            print(f"Success rate: {success_rate:.1%}")
            print(f"Total processed: {processed_messages}/{num_messages}")
            
            # Performance criteria
            min_send_rate = 500    # Should send at least 500 msg/sec
            min_process_rate = 200 # Should process at least 200 msg/sec
            min_success_rate = 0.8 # Should process at least 80% of messages
            
            if (send_rate >= min_send_rate and 
                process_rate >= min_process_rate and 
                success_rate >= min_success_rate):
                print("✓ Performance benchmark PASSED")
                return True
            else:
                print("✗ Performance benchmark FAILED")
                print(f"  Send rate: {send_rate:.0f} >= {min_send_rate} ({'✓' if send_rate >= min_send_rate else '✗'})")
                print(f"  Process rate: {process_rate:.0f} >= {min_process_rate} ({'✓' if process_rate >= min_process_rate else '✗'})")
                print(f"  Success rate: {success_rate:.1%} >= {min_success_rate:.1%} ({'✓' if success_rate >= min_success_rate else '✗'})")
                return False
                
        except Exception as e:
            print(f"✗ Performance benchmark failed: {e}")
            return False
            
    def test_protocol_chunking_integration(self, port):
        """Test ProtocolMessage-level chunking integration."""
        print("\n=== Test 6: ProtocolMessage Chunking Integration ===")
        
        try:
            client = TestClient('localhost', port)
            client.connect()
            self.clients.append(client)
            
            # Test various data sizes to trigger different chunking behaviors
            test_sizes = [
                ("small", 100),      # Single chunk
                ("medium", 2000),    # 2-3 chunks  
                ("large", 10000),    # Multiple chunks
                ("huge", 50000),     # Many chunks
            ]
            
            success_count = 0
            for size_name, size in test_sizes:
                test_data = "x" * size
                print(f"  Testing {size_name} data ({size} bytes)...")
                
                start_count = self.server.message_count
                
                # Send clipboard data of varying sizes
                success = client.send_clipboard_data(test_data, "text")
                
                if success:
                    # Wait for processing
                    time.sleep(0.5)
                    
                    # Check if message was processed
                    if self.server.message_count > start_count:
                        print(f"    ✓ {size_name} data processed successfully")
                        success_count += 1
                    else:
                        print(f"    ✗ {size_name} data not processed")
                else:
                    print(f"    ✗ Failed to send {size_name} data")
                    
            total_tests = len(test_sizes)
            if success_count == total_tests:
                print(f"✓ ProtocolMessage chunking test PASSED ({success_count}/{total_tests})")
                return True
            else:
                print(f"✗ ProtocolMessage chunking test FAILED ({success_count}/{total_tests})")
                return False
                
        except Exception as e:
            print(f"✗ ProtocolMessage chunking test failed: {e}")
            return False
            
    def run_all_tests(self):
        """Run all integration tests."""
        print("=" * 60)
        print("PyContinuity Server-Client Integration Test Suite")
        print("=" * 60)
        
        try:
            # Setup
            port = self.setup()
            
            # Run tests
            test_results = []
            
            test_results.append(("Basic Connectivity", self.test_basic_connectivity(port)))
            test_results.append(("Message Types", self.test_message_types(port)))
            test_results.append(("Large Message Chunking", self.test_chunking_large_messages(port)))
            test_results.append(("Concurrent Clients", self.test_concurrent_clients(port)))
            test_results.append(("Performance Benchmark", self.test_performance_benchmark(port)))
            test_results.append(("ProtocolMessage Chunking", self.test_protocol_chunking_integration(port)))
            
            # Results summary
            print("\n" + "=" * 60)
            print("TEST RESULTS SUMMARY")
            print("=" * 60)
            
            passed = 0
            total = len(test_results)
            
            for test_name, result in test_results:
                status = "PASSED" if result else "FAILED"
                icon = "✓" if result else "✗"
                print(f"{icon} {test_name}: {status}")
                if result:
                    passed += 1
                    
            print(f"\nOverall: {passed}/{total} tests passed")
            
            if passed == total:
                print("🎉 ALL TESTS PASSED! Server-client integration working correctly.")
                success = True
            else:
                print("❌ SOME TESTS FAILED! Please review the implementation.")
                success = False
                
        except Exception as e:
            print(f"Test suite error: {e}")
            import traceback
            traceback.print_exc()
            success = False
            
        finally:
            # Cleanup
            self.teardown()
            
        return success


def main():
    """Main entry point."""
    try:
        # Configure logging to reduce noise during testing
        logger = Logger.get_instance()
        
        # Run the test suite
        suite = IntegrationTestSuite()
        success = suite.run_all_tests()
        
        return 0 if success else 1
        
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        return 1
    except Exception as e:
        print(f"Test suite failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())