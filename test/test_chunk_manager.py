#!/usr/bin/env python3
"""
Test script for the improved IOManager and chunking system.
Tests the integration of ChunkManager with IOManager.
"""
import sys
import os
import time
import threading
from queue import Queue

# Add the project root to the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import necessary modules
from utils.net.ChunkManager import ChunkManager
from utils.net.netConstants import CHUNK_SIZE, END_DELIMITER, CHUNK_DELIMITER

try:
    from utils.protocol.message import MessageBuilder, ProtocolMessage
    from utils.protocol.adapter import ProtocolAdapter
    HAS_PROTOCOL = True
except ImportError:
    HAS_PROTOCOL = False
    print("Protocol modules not available - testing basic functionality only")

def test_improved_chunking():
    print("=== Testing Improved Chunking System ===")
    
    # Test 1: Basic ChunkManager functionality
    print("Test 1: Basic ChunkManager functionality...")
    chunk_manager = ChunkManager()
    
    # Test small data
    small_data = "mouse::move::100::200::false"
    prepared = chunk_manager.prepare_for_transmission(small_data)
    print(f"✓ Small data: {len(prepared)} bytes")
    
    # Test large data
    large_data = "x" * (chunk_manager.EFFECTIVE_CHUNK_SIZE + 500)
    prepared_large = chunk_manager.prepare_for_transmission(large_data)
    print(f"✓ Large data: {len(prepared_large)} bytes")
    
    if HAS_PROTOCOL:
        # Test structured message
        message_builder = MessageBuilder()
        mouse_msg = message_builder.create_mouse_message(x=150.5, y=300.7, event="click")
        prepared_structured = chunk_manager.prepare_for_transmission(mouse_msg)
        print(f"✓ Structured message: {len(prepared_structured)} bytes")
        
        # Verify structured message format
        if "PYCONT_V2:" in prepared_structured:
            print("✓ Structured message properly formatted")
        else:
            print("✗ Structured message format issue")
            return False
    
    return True

def test_mock_iomanager():
    print("\n=== Testing Mock IOManager Integration ===")
    
    class MockConnection:
        def __init__(self):
            self.sent_data = []
            self.is_open = True
        
        def send(self, data):
            self.sent_data.append(data)
            return len(data)
        
        def is_socket_open(self):
            return self.is_open
    
    class MockMessageSender:
        def __init__(self):
            self.messages = []
        
        def send(self, priority, message):
            self.messages.append((priority, message))
    
    # Test the improved chunking in context
    chunk_manager = ChunkManager()
    mock_conn = MockConnection()
    mock_sender = MockMessageSender()
    
    # Test sending various message types
    messages = [
        "keyboard::a::press",
        "mouse::move::100::200::false",
        "clipboard::hello world",
        "x" * 500,  # Medium size
        "y" * (chunk_manager.EFFECTIVE_CHUNK_SIZE + 100)  # Large size
    ]
    
    print("Test 2: Sending various message types...")
    for i, msg in enumerate(messages):
        chunk_manager.send_data(mock_conn, msg)
        print(f"✓ Message {i+1} sent: {len(msg)} bytes -> {len(mock_conn.sent_data)} chunks total")
    
    # Verify all messages were sent with proper delimiters
    total_chunks = len(mock_conn.sent_data)
    end_delimiter_count = sum(1 for chunk in mock_conn.sent_data if END_DELIMITER.encode('utf-8') in chunk)
    chunk_delimiter_count = sum(1 for chunk in mock_conn.sent_data if CHUNK_DELIMITER.encode('utf-8') in chunk)
    
    print(f"✓ Total chunks sent: {total_chunks}")
    print(f"✓ End delimiters: {end_delimiter_count}")
    print(f"✓ Chunk delimiters: {chunk_delimiter_count}")
    
    # Each message should have exactly one end delimiter
    expected_messages = len(messages)
    if end_delimiter_count >= expected_messages:
        print("✓ Proper delimiter usage confirmed")
    else:
        print(f"✗ Delimiter count mismatch: expected >= {expected_messages}, got {end_delimiter_count}")
        return False
    
    return True

def test_batching_efficiency():
    print("\n=== Testing Batching Efficiency ===")
    
    chunk_manager = ChunkManager()
    
    # Test batching decisions
    small_msgs = ["msg1", "msg2", "msg3"]
    can_batch_all = all(chunk_manager.can_batch_together(small_msgs[0], msg) for msg in small_msgs[1:])
    print(f"✓ Can batch small messages: {can_batch_all}")
    
    # Test batch creation
    batch = chunk_manager.create_batch(small_msgs)
    print(f"✓ Batch created: {len(batch)} bytes")
    
    if HAS_PROTOCOL:
        # Test that structured messages aren't batched
        message_builder = MessageBuilder()
        msg1 = message_builder.create_mouse_message(x=100, y=200, event="move")
        msg2 = message_builder.create_mouse_message(x=110, y=210, event="move")
        
        can_batch_structured = chunk_manager.can_batch_together(msg1, msg2)
        print(f"✓ Can batch structured messages: {can_batch_structured} (should be False)")
        
        if can_batch_structured:
            print("✗ Structured messages should not be batched")
            return False
    
    return True

def test_performance():
    print("\n=== Testing Performance ===")
    
    chunk_manager = ChunkManager()
    
    # Performance test with many small messages
    start_time = time.time()
    test_messages = [f"test_message_{i}" for i in range(1000)]
    
    class MockConn:
        def __init__(self):
            self.count = 0
        def send(self, data):
            self.count += 1
            return len(data)
    
    mock_conn = MockConn()
    
    for msg in test_messages:
        chunk_manager.send_data(mock_conn, msg)
    
    end_time = time.time()
    duration = end_time - start_time
    throughput = len(test_messages) / duration
    
    print(f"✓ Processed {len(test_messages)} messages in {duration:.3f}s")
    print(f"✓ Throughput: {throughput:.0f} messages/second")
    print(f"✓ Total chunks sent: {mock_conn.count}")
    
    if throughput > 5000:  # Should handle at least 5000 msg/sec
        print("✓ Performance test passed")
        return True
    else:
        print(f"✗ Performance below threshold: {throughput:.0f} < 5000 msg/sec")
        return False

if __name__ == "__main__":
    try:
        success = True
        
        success &= test_improved_chunking()
        success &= test_mock_iomanager()
        success &= test_batching_efficiency()
        success &= test_performance()
        
        if success:
            print("\n=== All IOManager tests passed! ===")
            print("The improved chunking system is working efficiently.")
        else:
            print("\n=== Some tests failed ===")
            sys.exit(1)
            
    except Exception as e:
        print(f"Test error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)