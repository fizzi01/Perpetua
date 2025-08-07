#!/usr/bin/env python3
"""
Test script for the improved protocol implementation.
Tests message ordering, protocol conversion, and timing behavior.
"""

import sys
import os
import time
import threading

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.protocol.message import MessageBuilder, ProtocolMessage
from utils.protocol.adapter import ProtocolAdapter
from utils.protocol.ordering import OrderedMessageProcessor


def test_mouse_ordering_scenario():
    """Test scenario simulating Windows cursor smoothness issues."""
    print("Testing mouse event ordering scenario...")
    
    builder = MessageBuilder()
    adapter = ProtocolAdapter()
    processed_messages = []
    
    def process_message(message: ProtocolMessage):
        """Callback to collect processed messages."""
        processed_messages.append(message)
        print(f"Processed: {message.message_type} at {message.timestamp:.4f} "
              f"pos=({message.payload.get('x', 0)}, {message.payload.get('y', 0)})")
    
    # Create ordered processor with smaller tolerance for faster testing
    processor = OrderedMessageProcessor(
        process_callback=process_message,
        max_delay_tolerance=0.03  # 30ms tolerance for testing
    )
    processor.start()
    
    # Simulate rapid mouse movements that arrive out of order
    base_time = time.time() - 0.5  # Set base time in the past to ensure all messages are "ready"
    mouse_events = [
        (base_time + 0.1, 100, 100, 'move'),
        (base_time + 0.2, 110, 105, 'move'),  
        (base_time + 0.3, 120, 110, 'move'),
        (base_time + 0.4, 130, 115, 'move'),
        (base_time + 0.5, 140, 120, 'move'),
    ]
    
    # Send ALL messages first, then wait for processing
    print(f"Sending {len(mouse_events)} mouse events out of order...")
    for timestamp, x, y, event in reversed(mouse_events):  # Reverse order
        msg = builder.create_mouse_message(x, y, event)
        msg.timestamp = timestamp  # Set specific timestamp
        processor.add_message(msg)
        print(f"  Queued: pos=({x}, {y}) at {timestamp:.4f}")
    
    # Wait for processing - give time for ordering to work
    print("Waiting for ordered processing...")
    time.sleep(0.1)  # Wait longer than delay tolerance
    processor.stop()
    
    # Verify ordering
    print(f"\nProcessed {len(processed_messages)} messages")
    if len(processed_messages) >= 2:
        timestamps = [msg.timestamp for msg in processed_messages]
        is_ordered = all(
            timestamps[i] <= timestamps[i+1] 
            for i in range(len(timestamps)-1)
        )
        print(f"Timestamps: {[f'{ts:.4f}' for ts in timestamps]}")
        print(f"Messages processed in chronological order: {is_ordered}")
        
        if is_ordered:
            print("✓ Mouse ordering test PASSED")
            return True
        else:
            print("✗ Mouse ordering test FAILED")
            return False
    else:
        print("✗ Not enough messages processed")
        return False


def test_protocol_conversion():
    """Test protocol conversion between legacy and structured formats."""
    print("\nTesting protocol conversion...")
    
    adapter = ProtocolAdapter()
    builder = MessageBuilder()
    
    # Test cases: (legacy_command, expected_type, expected_payload_check)
    test_cases = [
        ("mouse move 100 200 false", "mouse", lambda p: p['x'] == 100.0 and p['y'] == 200.0),
        ("keyboard press a", "keyboard", lambda p: p['key'] == 'a' and p['event'] == 'press'),
        ("clipboard hello", "clipboard", lambda p: p['content'] == 'hello'),
        ("file_start test.txt 1024", "file", lambda p: p['command'] == 'file_start' and p['file_name'] == 'test.txt'),
    ]
    
    conversion_results = []
    for legacy_cmd, expected_type, payload_check in test_cases:
        # Legacy to structured
        structured = adapter.legacy_to_structured(legacy_cmd)
        if not structured:
            print(f"✗ Failed to convert legacy command: {legacy_cmd}")
            conversion_results.append(False)
            continue
            
        if structured.message_type != expected_type:
            print(f"✗ Wrong message type for {legacy_cmd}: got {structured.message_type}, expected {expected_type}")
            conversion_results.append(False)
            continue
            
        if not payload_check(structured.payload):
            print(f"✗ Payload check failed for {legacy_cmd}: {structured.payload}")
            conversion_results.append(False)
            continue
            
        # Structured back to legacy
        back_to_legacy = adapter.structured_to_legacy(structured)
        
        # Test structured format encoding/decoding
        encoded = adapter.encode_structured_message(structured)
        decoded = adapter.decode_structured_message(encoded)
        
        if decoded.message_type != structured.message_type:
            print(f"✗ Encoding/decoding failed for {legacy_cmd}")
            conversion_results.append(False)
            continue
            
        print(f"✓ Conversion test passed for {expected_type}: {legacy_cmd}")
        conversion_results.append(True)
    
    all_passed = all(conversion_results)
    print(f"Protocol conversion tests: {'PASSED' if all_passed else 'FAILED'}")
    return all_passed


def test_performance():
    """Test performance characteristics of the new protocol."""
    print("\nTesting protocol performance...")
    
    builder = MessageBuilder()
    adapter = ProtocolAdapter()
    
    # Test message creation speed
    start_time = time.time()
    num_messages = 1000
    
    for i in range(num_messages):
        msg = builder.create_mouse_message(i, i+1, 'move')
        encoded = adapter.encode_structured_message(msg)
        decoded = adapter.decode_structured_message(encoded)
    
    end_time = time.time()
    duration = end_time - start_time
    messages_per_sec = num_messages / duration
    
    print(f"Created, encoded, and decoded {num_messages} messages in {duration:.3f}s")
    print(f"Performance: {messages_per_sec:.0f} messages/second")
    
    # Check if performance is acceptable (should handle mouse events at 60+ FPS)
    min_required_fps = 60
    if messages_per_sec >= min_required_fps:
        print(f"✓ Performance test PASSED (meets {min_required_fps} FPS requirement)")
        return True
    else:
        print(f"✗ Performance test FAILED (below {min_required_fps} FPS requirement)")
        return False


def main():
    """Run all protocol tests."""
    print("Running PyContinuity Protocol Improvement Tests")
    print("=" * 50)
    
    test_results = []
    
    # Run tests
    test_results.append(test_protocol_conversion())
    test_results.append(test_mouse_ordering_scenario())
    test_results.append(test_performance())
    
    # Summary
    print("\n" + "=" * 50)
    passed_tests = sum(test_results)
    total_tests = len(test_results)
    
    print(f"Test Results: {passed_tests}/{total_tests} tests passed")
    
    if passed_tests == total_tests:
        print("🎉 All tests PASSED! Protocol implementation is working correctly.")
        return 0
    else:
        print("❌ Some tests FAILED. Please review the implementation.")
        return 1


if __name__ == "__main__":
    sys.exit(main())