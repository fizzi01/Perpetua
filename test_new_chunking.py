#!/usr/bin/env python3
"""
Test the new ProtocolMessage-level chunking system.
"""
import io
import json
import socket
import threading
import time
from unittest.mock import Mock

# Import the necessary modules
from utils.net.ChunkManager import ChunkManager
from utils.protocol.message import ProtocolMessage, MessageBuilder


class MockSocket:
    """Mock socket for testing."""
    
    def __init__(self):
        self.sent_data = b''
        self.receive_buffer = b''
        self.is_open = True
    
    def send(self, data):
        """Simulate sending data."""
        self.sent_data += data
        return len(data)
    
    def recv(self, size):
        """Simulate receiving data."""
        if len(self.receive_buffer) >= size:
            data = self.receive_buffer[:size]
            self.receive_buffer = self.receive_buffer[size:]
            return data
        else:
            data = self.receive_buffer
            self.receive_buffer = b''
            return data
    
    def add_incoming_data(self, data):
        """Add data to the receive buffer."""
        self.receive_buffer += data


def test_protocol_message_serialization():
    """Test ProtocolMessage binary serialization."""
    print("Test 1: ProtocolMessage serialization...")
    
    builder = MessageBuilder()
    message = builder.create_mouse_message(x=100.5, y=200.7, event="move")
    
    # Test to_bytes and from_bytes
    message_bytes = message.to_bytes()
    reconstructed = ProtocolMessage.from_bytes(message_bytes)
    
    assert message.message_type == reconstructed.message_type
    assert message.payload == reconstructed.payload
    assert abs(message.timestamp - reconstructed.timestamp) < 0.001
    
    print(f"✓ Serialized to {len(message_bytes)} bytes")
    print(f"✓ Deserialization successful")
    return True


def test_chunk_creation():
    """Test creating chunks from large messages."""
    print("\nTest 2: ProtocolMessage chunking...")
    
    builder = MessageBuilder()
    
    # Create a large payload that will require chunking
    large_payload = {
        "data": "x" * 10000,  # Large data
        "additional_info": "test" * 1000
    }
    
    large_message = ProtocolMessage(
        message_type="test",
        timestamp=time.time(),
        sequence_id=1,
        payload=large_payload
    )
    
    print(f"Original message size: {large_message.get_serialized_size()} bytes")
    
    # Test chunking
    chunk_size = 4096
    chunks = builder.create_chunked_message(large_message, chunk_size)
    
    print(f"✓ Split into {len(chunks)} chunks")
    
    # Verify all chunks are under size limit
    for i, chunk in enumerate(chunks):
        chunk_size_actual = chunk.get_serialized_size()
        print(f"  Chunk {i}: {chunk_size_actual} bytes")
        assert chunk_size_actual <= chunk_size, f"Chunk {i} too large: {chunk_size_actual} > {chunk_size}"
        assert chunk.is_chunk == True
        assert chunk.message_id is not None
        assert chunk.chunk_index == i
        assert chunk.total_chunks == len(chunks)
    
    # Test reconstruction
    reconstructed = builder.reconstruct_from_chunks(chunks)
    
    assert reconstructed.message_type == large_message.message_type
    assert reconstructed.payload == large_message.payload
    assert not reconstructed.is_chunk
    
    print("✓ Chunking and reconstruction successful")
    return True


def test_chunk_manager_integration():
    """Test ChunkManager with ProtocolMessage chunking."""
    print("\nTest 3: ChunkManager integration...")
    
    chunk_manager = ChunkManager()
    mock_socket = MockSocket()
    
    # Create test message
    builder = MessageBuilder()
    message = builder.create_mouse_message(x=150, y=250, event="click")
    
    # Send message
    chunk_manager.send_data(mock_socket, message)
    sent_data = mock_socket.sent_data
    
    print(f"✓ Sent {len(sent_data)} bytes")
    
    # Receive message
    messages, bytes_consumed = chunk_manager.receive_data(sent_data)
    
    assert len(messages) == 1
    assert bytes_consumed == len(sent_data)
    
    received_message = messages[0]
    assert isinstance(received_message, ProtocolMessage)
    assert received_message.message_type == "mouse"
    assert received_message.payload["x"] == 150
    assert received_message.payload["y"] == 250
    assert received_message.payload["event"] == "click"
    
    print("✓ Message sent and received correctly")
    return True


def test_large_message_chunking():
    """Test chunking of large messages through ChunkManager."""
    print("\nTest 4: Large message chunking through ChunkManager...")
    
    chunk_manager = ChunkManager()
    mock_socket = MockSocket()
    
    # Create large message
    large_payload = {"data": "x" * 20000}  # Very large payload
    large_message = ProtocolMessage(
        message_type="large_test",
        timestamp=time.time(),
        sequence_id=1,
        payload=large_payload
    )
    
    original_size = large_message.get_serialized_size()
    print(f"Original message size: {original_size} bytes")
    
    # Send large message
    chunk_manager.send_data(mock_socket, large_message)
    sent_data = mock_socket.sent_data
    
    print(f"✓ Sent {len(sent_data)} bytes total")
    
    # Receive and reconstruct
    messages, bytes_consumed = chunk_manager.receive_data(sent_data)
    
    assert len(messages) == 1
    assert bytes_consumed == len(sent_data)
    
    received_message = messages[0]
    assert isinstance(received_message, ProtocolMessage)
    assert received_message.message_type == "large_test"
    assert received_message.payload == large_payload
    
    print("✓ Large message chunked and reconstructed correctly")
    return True


def test_legacy_data_handling():
    """Test handling of legacy string data."""
    print("\nTest 5: Legacy data handling...")
    
    chunk_manager = ChunkManager()
    mock_socket = MockSocket()
    
    # Send legacy string
    legacy_data = "mouse::move::100::200::false"
    chunk_manager.send_data(mock_socket, legacy_data)
    
    sent_data = mock_socket.sent_data
    print(f"✓ Sent legacy data: {len(sent_data)} bytes")
    
    # Receive
    messages, bytes_consumed = chunk_manager.receive_data(sent_data)
    
    assert len(messages) == 1
    received_message = messages[0]
    
    # Should be wrapped in a ProtocolMessage
    assert isinstance(received_message, ProtocolMessage)
    assert received_message.message_type == "legacy"
    assert received_message.payload["data"] == legacy_data
    
    print("✓ Legacy data properly wrapped and handled")
    return True


def main():
    """Run all tests."""
    print("=== Testing New ProtocolMessage-Level Chunking ===")
    
    tests = [
        test_protocol_message_serialization,
        test_chunk_creation, 
        test_chunk_manager_integration,
        test_large_message_chunking,
        test_legacy_data_handling
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
        print("🎉 All tests PASSED! New chunking system works correctly.")
        return True
    else:
        print("❌ Some tests failed.")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)