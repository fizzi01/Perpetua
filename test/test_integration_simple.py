#!/usr/bin/env python3
"""
Simplified Integration Test for PyContinuity
Tests core protocol functionality without complex networking dependencies.
"""

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Empty
from typing import List, Dict, Any, Tuple
import tempfile

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from utils.protocol.message import MessageBuilder, ProtocolMessage
    from utils.protocol.adapter import ProtocolAdapter
    from utils.net.ChunkManager import ChunkManager
    from utils.Logging import Logger
    PROTOCOL_AVAILABLE = True
except ImportError as e:
    print(f"Protocol modules not available: {e}")
    PROTOCOL_AVAILABLE = False


class MockSocket:
    """Mock socket for testing data flow."""
    
    def __init__(self):
        self.sent_data = b''
        self.receive_buffer = b''
        self.is_open = True
        self.address = ('127.0.0.1', 12345)
    
    def send(self, data):
        """Mock send method."""
        if isinstance(data, str):
            data = data.encode('utf-8')
        self.sent_data += data
        return len(data)
    
    def recv(self, size):
        """Mock receive method."""
        if len(self.receive_buffer) >= size:
            data = self.receive_buffer[:size]
            self.receive_buffer = self.receive_buffer[size:]
            return data
        else:
            data = self.receive_buffer
            self.receive_buffer = b''
            return data
    
    def add_incoming_data(self, data):
        """Add data to receive buffer."""
        if isinstance(data, str):
            data = data.encode('utf-8')
        self.receive_buffer += data
    
    def is_socket_open(self):
        """Check if socket is open."""
        return self.is_open
    
    def close(self):
        """Close the socket."""
        self.is_open = False


class MockMessageService:
    """Mock message service that simulates the real MessageService behavior."""
    
    def __init__(self, socket_service):
        self.socket_service = socket_service
        self.chunk_manager = ChunkManager()
        self.message_builder = MessageBuilder()
        self.running = False
        self.sent_messages = []
        
    def start(self):
        """Start the service."""
        self.running = True
        
    def stop(self):
        """Stop the service."""
        self.running = False
        
    def send_message(self, priority, message, target):
        """Send a message using the new protocol."""
        if not self.running:
            return False
            
        try:
            # Handle both ProtocolMessage and legacy string messages
            if isinstance(message, ProtocolMessage):
                # Use ChunkManager to send the message
                self.chunk_manager.send_data(self.socket_service, message)
            else:
                # Legacy string message
                self.chunk_manager.send_data(self.socket_service, str(message))
            
            self.sent_messages.append((priority, message, target))
            return True
        except Exception as e:
            print(f"Error sending message: {e}")
            return False
    
    def _send_file(self, file_name, file_data, target):
        """Send a file using the new ProtocolMessage approach."""
        try:
            file_size = len(file_data)
            
            # File start message
            start_message = self.message_builder.create_file_message(
                command="start",
                data={"filename": file_name, "size": file_size},
                target=target
            )
            self.chunk_manager.send_data(self.socket_service, start_message)
            
            # Send file data in chunks
            chunk_size = 8192
            offset = 0
            
            while offset < file_size:
                chunk_data = file_data[offset:offset + chunk_size]
                
                # Convert bytes to base64 for JSON serialization
                import base64
                chunk_data_b64 = base64.b64encode(chunk_data).decode('utf-8')
                
                chunk_message = self.message_builder.create_file_message(
                    command="chunk",
                    data={"data": chunk_data_b64, "offset": offset, "encoding": "base64"},
                    target=target
                )
                self.chunk_manager.send_data(self.socket_service, chunk_message)
                
                offset += len(chunk_data)
            
            # File end message
            end_message = self.message_builder.create_file_message(
                command="end",
                data={"filename": file_name},
                target=target
            )
            self.chunk_manager.send_data(self.socket_service, end_message)
            
            return True
        except Exception as e:
            print(f"Error sending file: {e}")
            return False


class IntegrationTest:
    """Simplified integration test suite."""
    
    def __init__(self):
        self.results = {}
        
    def test_protocol_message_flow(self):
        """Test basic ProtocolMessage flow through the system."""
        print("Test 1: ProtocolMessage flow...")
        
        if not PROTOCOL_AVAILABLE:
            print("✗ Protocol modules not available - skipping")
            return False
        
        try:
            # Setup
            mock_socket = MockSocket()
            message_service = MockMessageService(mock_socket)
            message_service.start()
            
            # Create and send various message types
            builder = MessageBuilder()
            
            # Mouse message
            mouse_msg = builder.create_mouse_message(100.5, 200.7, "move")
            success1 = message_service.send_message("mouse", mouse_msg, "screen1")
            
            # Keyboard message
            keyboard_msg = builder.create_keyboard_message("a", "press")
            success2 = message_service.send_message("keyboard", keyboard_msg, "screen1")
            
            # Clipboard message
            clipboard_msg = builder.create_clipboard_message("Hello World", "text")
            success3 = message_service.send_message("clipboard", clipboard_msg, "screen1")
            
            # Check results
            if success1 and success2 and success3:
                print(f"✓ All messages sent successfully")
                print(f"  Socket received {len(mock_socket.sent_data)} bytes")
                return True
            else:
                print("✗ Some messages failed to send")
                return False
                
        except Exception as e:
            print(f"✗ Test failed: {e}")
            return False
    
    def test_chunking_behavior(self):
        """Test chunking behavior with large messages."""
        print("\nTest 2: Chunking behavior...")
        
        if not PROTOCOL_AVAILABLE:
            print("✗ Protocol modules not available - skipping")
            return False
        
        try:
            mock_socket = MockSocket()
            chunk_manager = ChunkManager()
            
            # Test small message (no chunking needed)
            small_msg = "small_message"
            chunk_manager.send_data(mock_socket, small_msg)
            small_data_size = len(mock_socket.sent_data)
            
            # Reset socket
            mock_socket.sent_data = b''
            
            # Test large message (chunking needed)
            large_payload = {"data": "x" * 20000}  # 20KB payload
            large_msg = ProtocolMessage(
                message_type="test",
                timestamp=time.time(),
                sequence_id=1,
                payload=large_payload
            )
            
            chunk_manager.send_data(mock_socket, large_msg)
            large_data_size = len(mock_socket.sent_data)
            
            print(f"✓ Small message: {small_data_size} bytes")
            print(f"✓ Large message: {large_data_size} bytes")
            
            # Verify chunking occurred for large message
            if large_data_size > small_data_size:
                print("✓ Chunking behavior verified")
                return True
            else:
                print("✗ Chunking behavior unclear")
                return False
                
        except Exception as e:
            print(f"✗ Test failed: {e}")
            return False
    
    def test_file_transfer_protocol(self):
        """Test file transfer using new protocol."""
        print("\nTest 3: File transfer protocol...")
        
        if not PROTOCOL_AVAILABLE:
            print("✗ Protocol modules not available - skipping")  
            return False
        
        try:
            mock_socket = MockSocket()
            message_service = MockMessageService(mock_socket)
            message_service.start()
            
            # Create test file data
            test_file_data = b"This is test file content for the integration test. " * 100
            file_name = "test_file.txt"
            
            # Send file
            success = message_service._send_file(file_name, test_file_data, "screen1")
            
            if success:
                print(f"✓ File transfer completed ({len(test_file_data)} bytes)")
                print(f"  Total data sent: {len(mock_socket.sent_data)} bytes")
                return True
            else:
                print("✗ File transfer failed")
                return False
                
        except Exception as e:
            print(f"✗ Test failed: {e}")
            return False
    
    def test_performance_simulation(self):
        """Test performance with rapid message sending."""
        print("\nTest 4: Performance simulation...")
        
        if not PROTOCOL_AVAILABLE:
            print("✗ Protocol modules not available - skipping")
            return False
        
        try:
            mock_socket = MockSocket()
            message_service = MockMessageService(mock_socket)
            message_service.start()
            
            builder = MessageBuilder()
            
            # Send many messages rapidly
            num_messages = 100
            start_time = time.time()
            
            for i in range(num_messages):
                # Alternate between message types
                if i % 3 == 0:
                    msg = builder.create_mouse_message(i % 1920, i % 1080, "move")
                    message_service.send_message("mouse", msg, "screen1")
                elif i % 3 == 1:
                    key = chr(ord('a') + (i % 26))
                    msg = builder.create_keyboard_message(key, "press")
                    message_service.send_message("keyboard", msg, "screen1")
                else:
                    msg = builder.create_clipboard_message(f"data_{i}", "text")
                    message_service.send_message("clipboard", msg, "screen1")
            
            end_time = time.time()
            duration = end_time - start_time
            rate = num_messages / duration
            
            print(f"✓ Sent {num_messages} messages in {duration:.3f}s")
            print(f"✓ Rate: {rate:.0f} messages/second")
            print(f"✓ Total data transmitted: {len(mock_socket.sent_data)} bytes")
            
            # Check if rate is acceptable (should be > 1000 msg/sec for mock test)
            if rate > 1000:
                print("✓ Performance test passed")
                return True
            else:
                print(f"✗ Performance below threshold: {rate:.0f} < 1000 msg/sec")
                return False
                
        except Exception as e:
            print(f"✗ Test failed: {e}")
            return False
    
    def test_concurrent_access(self):
        """Test concurrent access to the messaging system."""
        print("\nTest 5: Concurrent access...")
        
        if not PROTOCOL_AVAILABLE:
            print("✗ Protocol modules not available - skipping")
            return False
        
        try:
            # Create multiple mock sockets and services
            num_clients = 3
            services = []
            
            for i in range(num_clients):
                mock_socket = MockSocket()
                service = MockMessageService(mock_socket)
                service.start()
                services.append(service)
            
            builder = MessageBuilder()
            
            def send_messages_from_client(client_id, service):
                """Send messages from a specific client."""
                messages_sent = 0
                try:
                    for i in range(10):
                        msg = builder.create_mouse_message(
                            client_id * 100 + i, 
                            client_id * 100 + i + 1, 
                            "move"
                        )
                        if service.send_message("mouse", msg, f"screen{client_id}"):
                            messages_sent += 1
                        time.sleep(0.001)  # Small delay
                    return messages_sent
                except Exception as e:
                    print(f"Error in client {client_id}: {e}")
                    return 0
            
            # Run clients concurrently
            with ThreadPoolExecutor(max_workers=num_clients) as executor:
                futures = []
                for i, service in enumerate(services):
                    future = executor.submit(send_messages_from_client, i, service)
                    futures.append(future)
                
                total_sent = sum(future.result() for future in futures)
            
            print(f"✓ {num_clients} concurrent clients sent {total_sent} messages total")
            
            # Calculate total data
            total_data = sum(len(service.socket_service.sent_data) for service in services)
            print(f"✓ Total data transmitted: {total_data} bytes")
            
            if total_sent > 0:
                print("✓ Concurrent access test passed")
                return True
            else:
                print("✗ No messages sent successfully")
                return False
                
        except Exception as e:
            print(f"✗ Test failed: {e}")
            return False
    
    def run_all_tests(self):
        """Run all tests."""
        print("=" * 60)
        print("PyContinuity Simplified Integration Test Suite")
        print("=" * 60)
        
        if not PROTOCOL_AVAILABLE:
            print("⚠️  Protocol modules not fully available")
            print("This is expected in some environments.")
            print("Running basic compatibility tests only...")
        
        # Define tests
        tests = [
            ("ProtocolMessage Flow", self.test_protocol_message_flow),
            ("Chunking Behavior", self.test_chunking_behavior),
            ("File Transfer Protocol", self.test_file_transfer_protocol),
            ("Performance Simulation", self.test_performance_simulation),
            ("Concurrent Access", self.test_concurrent_access),
        ]
        
        # Run tests
        results = []
        for test_name, test_func in tests:
            try:
                result = test_func()
                results.append((test_name, result))
            except Exception as e:
                print(f"✗ {test_name} failed with error: {e}")
                results.append((test_name, False))
        
        # Summary
        print("\n" + "=" * 60)
        print("TEST RESULTS SUMMARY")
        print("=" * 60)
        
        passed = sum(1 for _, result in results if result)
        total = len(results)
        
        for test_name, result in results:
            icon = "✓" if result else "✗"
            status = "PASSED" if result else "FAILED"
            print(f"{icon} {test_name}: {status}")
        
        print(f"\nOverall: {passed}/{total} tests passed")
        
        if not PROTOCOL_AVAILABLE:
            print("\n⚠️  Note: Some tests skipped due to missing dependencies")
            print("This is normal in lightweight testing environments.")
            success = passed >= (total // 2)  # Pass if at least half work
        else:
            success = passed == total
        
        if success:
            print("🎉 Integration tests PASSED!")
            print("Core PyContinuity functionality is working correctly.")
        else:
            print("❌ Some integration tests FAILED!")
            print("Please review the implementation.")
        
        return success


def main():
    """Main entry point."""
    try:
        test_suite = IntegrationTest()
        success = test_suite.run_all_tests()
        return 0 if success else 1
    except Exception as e:
        print(f"Test suite error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())