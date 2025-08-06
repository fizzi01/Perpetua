#!/usr/bin/env python3
"""
Comprehensive test of the data flow with new ProtocolMessage chunking system.
Tests integration between IOManager, ChunkManager, and message handling.
"""
import tempfile
import threading
import time
from queue import Queue, Empty
from unittest.mock import Mock

from network.IOManager import MessageService, BaseMessageQueueManager
from utils.protocol.message import MessageBuilder, ProtocolMessage
from utils.net.ChunkManager import ChunkManager
from utils.Logging import Logger


# Initialize logger for testing
class MockLogger:
    def log(self, message, level=1):
        if level >= 2:  # Only print errors and warnings
            print(f"[LOG] {message}")

Logger._instance = MockLogger()


class MockConnection:
    """Mock network connection for testing."""
    
    def __init__(self):
        self.sent_data = b''
        self.received_queue = Queue()
        self.is_open = True
        
    def send(self, data):
        """Store sent data and simulate reception."""
        self.sent_data += data
        self.received_queue.put(data)
        return len(data)
    
    def recv(self, size):
        """Simulate receiving data."""
        try:
            return self.received_queue.get_nowait()
        except Empty:
            return b''
    
    def is_socket_open(self):
        return self.is_open


class MockContext:
    """Mock context for message queue manager."""
    
    def __init__(self):
        self.clients = {"test_client": MockConnection()}
        
    def get_connected_clients(self):
        return list(self.clients.keys())
    
    def get_client(self, key):
        return self.clients.get(key)
    
    def change_screen(self):
        pass


def test_end_to_end_data_flow():
    """Test complete data flow from MessageService through ChunkManager."""
    print("Test: End-to-end data flow...")
    
    # Setup components
    mock_context = MockContext()
    from network.IOManager import ServerMessageQueueManager
    queue_manager = ServerMessageQueueManager(mock_context)
    message_service = MessageService(queue_manager, mouse=True, keyboard=True, file=True)
    
    # Start components (but don't actually run threads in test)
    queue_manager._threads_started = True  # Fake that threads are started
    
    # Test mouse message
    builder = MessageBuilder()
    mouse_msg = builder.create_mouse_message(x=100, y=200, event="move", target="test_client")
    
    # Send through message service
    message_service.send_mouse("test_client", mouse_msg)
    
    # Process directly (simulate thread processing)
    screen, message = message_service.mouse_queue.get()
    assert screen == "test_client"
    assert isinstance(message, ProtocolMessage)
    assert message.message_type == "mouse"
    
    # Send through queue manager
    queue_manager._send_message((screen, message))
    
    # Check that data was sent to connection
    conn = mock_context.get_client("test_client")
    assert len(conn.sent_data) > 0
    
    # Test reception and parsing
    chunk_manager = ChunkManager()
    messages, bytes_consumed = chunk_manager.receive_data(conn.sent_data)
    
    assert len(messages) == 1
    received_message = messages[0]
    assert isinstance(received_message, ProtocolMessage)
    assert received_message.message_type == "mouse"
    assert received_message.payload["x"] == 100
    assert received_message.payload["y"] == 200
    
    print("✓ Mouse message flow successful")
    return True


def test_file_transfer_integration():
    """Test file transfer through the complete system."""
    print("\nTest: File transfer integration...")
    
    # Create test file
    test_content = b"Test file content for integration testing " * 50
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        temp_file.write(test_content)
        temp_file_path = temp_file.name
    
    try:
        # Setup components
        mock_context = MockContext()
        from network.IOManager import ServerMessageQueueManager
        queue_manager = ServerMessageQueueManager(mock_context)
        message_service = MessageService(queue_manager, file=True)
        
        # Start file transfer
        message_service._send_file(temp_file_path, "test_client")
        
        # Collect all sent data
        conn = mock_context.get_client("test_client")
        all_sent_data = conn.sent_data
        
        print(f"Debug: Total sent data: {len(all_sent_data)} bytes")
        if len(all_sent_data) == 0:
            print("Debug: No data was sent - checking message queue...")
            # The issue might be that we're not processing the message queue
            # Let's simulate the queue processing
            while not queue_manager.send_queue.is_empty():
                try:
                    _, message = queue_manager.send_queue.get(timeout=0.1)
                    queue_manager._send_message(message)
                except:
                    break
            all_sent_data = conn.sent_data
            print(f"Debug: After queue processing: {len(all_sent_data)} bytes")
        
        # Parse all messages
        chunk_manager = ChunkManager()
        offset = 0
        file_messages = []
        
        while offset < len(all_sent_data):
            remaining_data = all_sent_data[offset:]
            if len(remaining_data) < 4:
                break
                
            try:
                message = ProtocolMessage.from_bytes(remaining_data)
                message_size = message.get_serialized_size()
                offset += message_size
                
                if message.message_type == "file":
                    file_messages.append(message)
                elif message.is_chunk:
                    # Handle chunked messages
                    complete_message = chunk_manager.protocol_adapter.reassembler.add_chunk(message)
                    if complete_message and complete_message.message_type == "file":
                        file_messages.append(complete_message)
                        
            except Exception as e:
                print(f"Error parsing message at offset {offset}: {e}")
                break
        
        # Verify file transfer messages
        assert len(file_messages) >= 3, f"Expected at least 3 file messages, got {len(file_messages)}"
        
        # Check file_start
        start_msg = file_messages[0]
        assert start_msg.payload["command"] == "start"
        assert "filename" in start_msg.payload
        assert "size" in start_msg.payload
        
        # Check file_end
        end_msg = file_messages[-1]
        assert end_msg.payload["command"] == "end"
        
        # Check file_chunks
        chunk_msgs = [msg for msg in file_messages if msg.payload["command"] == "chunk"]
        assert len(chunk_msgs) > 0, "No file chunks found"
        
        print(f"✓ File transfer: {len(file_messages)} total messages")
        print(f"  - 1 file_start, {len(chunk_msgs)} file_chunks, 1 file_end")
        
        return True
        
    finally:
        import os
        os.unlink(temp_file_path)


def test_mixed_message_types():
    """Test handling of mixed ProtocolMessage and legacy data."""
    print("\nTest: Mixed message types...")
    
    mock_context = MockContext()
    from network.IOManager import ServerMessageQueueManager
    queue_manager = ServerMessageQueueManager(mock_context)
    
    # Test ProtocolMessage
    builder = MessageBuilder()
    protocol_msg = builder.create_keyboard_message(key="a", event="press")
    queue_manager._send_message(("test_client", protocol_msg))
    
    # Test legacy string
    legacy_msg = "mouse::move::150::250::false"
    queue_manager._send_message(("test_client", legacy_msg))
    
    # Check both were sent
    conn = mock_context.get_client("test_client")
    assert len(conn.sent_data) > 0
    
    # Parse received data
    chunk_manager = ChunkManager()
    messages, _ = chunk_manager.receive_data(conn.sent_data)
    
    # Should have both messages (legacy converted to ProtocolMessage)
    assert len(messages) >= 2
    
    # Both should be ProtocolMessage instances now
    for msg in messages:
        assert isinstance(msg, ProtocolMessage)
    
    print(f"✓ Mixed message handling: {len(messages)} messages processed")
    return True


def main():
    """Run comprehensive data flow tests."""
    print("=== Comprehensive Data Flow Testing ===")
    
    tests = [
        test_end_to_end_data_flow,
        test_file_transfer_integration,
        test_mixed_message_types
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                print("✗ Test failed")
        except Exception as e:
            print(f"✗ Test error: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n=== Results: {passed}/{total} tests passed ===")
    
    if passed == total:
        print("🎉 All comprehensive tests PASSED! Data flow works correctly.")
        return True
    else:
        print("❌ Some tests failed.")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)