#!/usr/bin/env python3
"""
Test file transfer functionality with the new ProtocolMessage-based chunking.
"""
import os
import tempfile
import base64
import zlib
from unittest.mock import Mock

from network.IOManager import MessageService
from utils.protocol.message import MessageBuilder, ProtocolMessage
from utils.net.ChunkManager import ChunkManager
from utils.Logging import Logger


# Initialize logger for testing
class MockLogger:
    def log(self, message, level=1):
        if level >= 2:  # Only print errors and warnings
            print(f"[LOG] {message}")

Logger._instance = MockLogger()


class MockMessageSender:
    """Mock message sender to capture sent messages."""
    
    def __init__(self):
        self.sent_messages = []
        self.alive = True
    
    def send(self, priority, message):
        """Capture sent message."""
        self.sent_messages.append((priority, message))
    
    def is_alive(self):
        return self.alive
    
    def start(self):
        pass
    
    def join(self):
        pass


def test_file_transfer_with_protocol_messages():
    """Test file transfer using new ProtocolMessage format."""
    print("Test: File transfer with ProtocolMessage...")
    
    # Create a temporary test file
    test_content = b"This is test file content for testing the new ProtocolMessage-based file transfer system. " * 100
    
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        temp_file.write(test_content)
        temp_file_path = temp_file.name
    
    try:
        # Setup MessageService with mock sender
        mock_sender = MockMessageSender()
        message_service = MessageService(mock_sender, file=True)
        
        # Send the file
        message_service._send_file(temp_file_path, "test_screen")
        
        # Check sent messages
        messages = mock_sender.sent_messages
        print(f"✓ Sent {len(messages)} messages")
        
        # Verify message structure
        assert len(messages) >= 3, "Should have at least file_start, file_chunk(s), and file_end"
        
        # Check file_start message
        priority, (screen, start_message) = messages[0]
        assert priority == MessageService.FILE_PRIORITY
        assert screen == "test_screen"
        assert isinstance(start_message, ProtocolMessage)
        assert start_message.message_type == "file"
        assert start_message.payload["command"] == "start"
        assert "filename" in start_message.payload
        assert "size" in start_message.payload
        print("✓ file_start message is correct ProtocolMessage")
        
        # Check file_chunk messages
        chunk_messages = []
        for i in range(1, len(messages) - 1):
            priority, (screen, chunk_message) = messages[i]
            assert priority == MessageService.FILE_PRIORITY
            assert screen == "test_screen"
            assert isinstance(chunk_message, ProtocolMessage)
            assert chunk_message.message_type == "file"
            assert chunk_message.payload["command"] == "chunk"
            assert "data" in chunk_message.payload
            assert "index" in chunk_message.payload
            chunk_messages.append(chunk_message)
        print(f"✓ {len(chunk_messages)} file_chunk messages are correct ProtocolMessages")
        
        # Check file_end message
        priority, (screen, end_message) = messages[-1]
        assert priority == MessageService.FILE_PRIORITY
        assert screen == "test_screen"
        assert isinstance(end_message, ProtocolMessage)
        assert end_message.message_type == "file"
        assert end_message.payload["command"] == "end"
        assert "filename" in end_message.payload
        print("✓ file_end message is correct ProtocolMessage")
        
        # Verify we can reconstruct the file from chunks
        reconstructed_data = b''
        for chunk_msg in chunk_messages:
            encoded_chunk = chunk_msg.payload["data"]
            compressed_chunk = base64.b64decode(encoded_chunk.encode())
            chunk_data = zlib.decompress(compressed_chunk)
            reconstructed_data += chunk_data
        
        assert reconstructed_data == test_content
        print("✓ File content can be reconstructed from chunks")
        
        return True
        
    finally:
        # Clean up
        os.unlink(temp_file_path)


def test_chunk_manager_with_file_messages():
    """Test that ChunkManager properly handles file ProtocolMessages."""
    print("\nTest: ChunkManager with file messages...")
    
    builder = MessageBuilder()
    chunk_manager = ChunkManager()
    
    # Create a file message
    file_message = builder.create_file_message(
        command="start",
        data={"filename": "test.txt", "size": 1024}
    )
    
    # Serialize and deserialize
    message_bytes = file_message.to_bytes()
    messages, bytes_consumed = chunk_manager.receive_data(message_bytes)
    
    assert len(messages) == 1
    assert bytes_consumed == len(message_bytes)
    
    received_message = messages[0]
    assert isinstance(received_message, ProtocolMessage)
    assert received_message.message_type == "file"
    assert received_message.payload["command"] == "start"
    assert received_message.payload["filename"] == "test.txt"
    assert received_message.payload["size"] == 1024
    
    print("✓ ChunkManager properly handles file ProtocolMessages")
    return True


def test_large_file_chunking():
    """Test that large file messages are properly chunked."""
    print("\nTest: Large file message chunking...")
    
    builder = MessageBuilder()
    
    # Create a large file chunk message (simulating a large file chunk)
    large_chunk_data = base64.b64encode(b"x" * 10000).decode()  # Large encoded data
    
    large_file_message = builder.create_file_message(
        command="chunk",
        data={
            "data": large_chunk_data,
            "index": 0
        }
    )
    
    print(f"Original file message size: {large_file_message.get_serialized_size()} bytes")
    
    # Test chunking if needed
    chunk_size = 4096
    if large_file_message.get_serialized_size() > chunk_size:
        chunks = builder.create_chunked_message(large_file_message, chunk_size)
        print(f"✓ Large file message split into {len(chunks)} chunks")
        
        # Verify reconstruction
        reconstructed = builder.reconstruct_from_chunks(chunks)
        assert reconstructed.message_type == "file"
        assert reconstructed.payload["command"] == "chunk"
        assert reconstructed.payload["data"] == large_chunk_data
        print("✓ Large file message successfully reconstructed")
    else:
        print("✓ File message fits in single chunk")
    
    return True


def main():
    """Run all file transfer tests."""
    print("=== Testing File Transfer with New ProtocolMessage System ===")
    
    tests = [
        test_file_transfer_with_protocol_messages,
        test_chunk_manager_with_file_messages,
        test_large_file_chunking
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
        print("🎉 All file transfer tests PASSED! New system works correctly.")
        return True
    else:
        print("❌ Some tests failed.")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)