#!/usr/bin/env python3
"""
Final comprehensive test for clipboard chunking reconstruction.
Tests the core logic without full ClientHandler setup.
"""

import sys
import os
sys.path.append('/home/runner/work/PyContinuity/PyContinuity')

from utils.command.ClipboardCommand import ClipboardCommand
from utils.protocol.message import MessageBuilder, ProtocolMessage
from utils.protocol.chunking import ChunkReassembler
from utils.protocol.adapter import ProtocolAdapter
from utils.net.ChunkManager import ChunkManager
import time


def test_clipboard_reconstruction_complete_flow():
    """Test the complete clipboard reconstruction flow that matches ServerHandler logic."""
    print("=== Complete Clipboard Reconstruction Test ===")
    
    # Create comprehensive clipboard content
    clipboard_content = """
Multi-line clipboard test with challenging content:

🎉 Unicode characters: áéíóú àèìòù âêîôû ãñõ
🚀 Emojis and symbols: ☀️ ☁️ ⭐ 🌟 💫 ∑ ∏ ∆ ∇ ∞ ≠ ≤ ≥
📝 Special syntax: { "json": "value" } <xml attr="test"/> 
💻 Code: function test() { return "hello\\nworld"; }
🌍 Languages: 中文 日本語 한국어 русский العربية हिन्दी
📁 Paths: C:\\Users\\Test\\file.txt /home/user/file.txt
🔗 URLs: https://example.com?param=value&test=123#anchor
💾 Base64-like: SGVsbG8gV29ybGQhIFRoaXMgaXM=
⚠️ Control chars: Line1\tTab\nLine2\rCarriageReturn

""" + "Large content padding: " + "X" * 1200
    
    print(f"Original clipboard content length: {len(clipboard_content)}")
    print(f"Content preview: {repr(clipboard_content[:100])}...")
    
    # Step 1: Create clipboard command and protocol message (client side)
    clipboard_cmd = ClipboardCommand.create(clipboard_content)
    original_protocol_msg = clipboard_cmd.to_protocol_message(source="client", target="server")
    
    print(f"Original protocol message size: {original_protocol_msg.get_serialized_size()} bytes")
    
    # Step 2: Client-side chunking simulation
    client_chunk_manager = ChunkManager(chunk_size=1024)
    
    # Force chunking if message is large
    if original_protocol_msg.get_serialized_size() > 1024:
        chunks = client_chunk_manager.message_builder.create_chunked_message(original_protocol_msg, 1024)
        print(f"Client created {len(chunks)} chunks")
        
        # Serialize each chunk to bytes (transmission simulation)
        transmitted_bytes = b''
        for i, chunk in enumerate(chunks):
            chunk_bytes = chunk.to_bytes()
            transmitted_bytes += chunk_bytes
            print(f"  Chunk {i}: {len(chunk_bytes)} bytes, is_chunk={chunk.is_chunk}")
    else:
        print("Message doesn't need chunking")
        return
    
    print(f"Total transmitted: {len(transmitted_bytes)} bytes")
    
    # Step 3: Server-side reception simulation (like ClientHandler._handle)
    print("\n--- Server-side Processing ---")
    
    server_chunk_manager = ChunkManager()
    
    # Simulate receiving data buffer by buffer (as happens in real network reception)
    data_buffer = b''
    received_messages = []
    
    # Simulate receiving in multiple network packets
    packet_size = 512
    for i in range(0, len(transmitted_bytes), packet_size):
        packet = transmitted_bytes[i:i + packet_size]
        data_buffer += packet
        
        # Process complete messages from buffer
        complete_messages, bytes_consumed = server_chunk_manager.receive_data(data_buffer)
        
        if bytes_consumed > 0:
            data_buffer = data_buffer[bytes_consumed:]
            
        for msg in complete_messages:
            received_messages.append(msg)
        
        print(f"Packet {i//packet_size + 1}: {len(packet)} bytes, buffer: {len(data_buffer)} bytes, complete: {len(complete_messages)}")
    
    print(f"Server received {len(received_messages)} complete messages")
    
    # Step 4: Message processing simulation (like ClientHandler._process_batch)
    if received_messages:
        reconstructed_message = received_messages[0]
        
        if isinstance(reconstructed_message, ProtocolMessage):
            print(f"Reconstructed message type: {reconstructed_message.message_type}")
            print(f"Payload keys: {list(reconstructed_message.payload.keys())}")
            
            # Step 5: Convert to legacy format (like _process_ordered_message)
            protocol_adapter = ProtocolAdapter()
            legacy_command = protocol_adapter.structured_to_legacy(reconstructed_message)
            
            print(f"Legacy command length: {len(legacy_command)}")
            print(f"Legacy command preview: {repr(legacy_command[:100])}...")
            
            # Step 6: Verify content integrity
            reconstructed_content = reconstructed_message.payload.get("content", "")
            
            if reconstructed_content == clipboard_content:
                print("✓ COMPLETE FLOW SUCCESSFUL - Content perfectly reconstructed")
            else:
                print("✗ Content mismatch detected!")
                print(f"Original length: {len(clipboard_content)}")
                print(f"Reconstructed length: {len(reconstructed_content)}")
                
                # Find first difference
                for i, (orig, recon) in enumerate(zip(clipboard_content, reconstructed_content)):
                    if orig != recon:
                        print(f"First difference at position {i}: {repr(orig)} vs {repr(recon)}")
                        break
                        
                # Show end comparison
                if len(clipboard_content) != len(reconstructed_content):
                    print(f"Length difference: {len(clipboard_content) - len(reconstructed_content)}")
                    
        else:
            print(f"✗ Unexpected message type: {type(reconstructed_message)}")
    else:
        print("✗ No messages reconstructed")


def test_protocol_adapter_clipboard_parsing():
    """Test the specific protocol adapter clipboard parsing that was fixed."""
    print("\n=== Protocol Adapter Clipboard Parsing Test ===")
    
    test_cases = [
        "Simple clipboard content",
        "Content with spaces and special chars: áéíóú",
        "Multi word content with many spaces",
        "Content with \"quotes\" and 'apostrophes'",
        "Content\nwith\nmultiple\nlines",
        "Content with\ttabs\tand\tstuff",
        "JSON-like: {\"key\": \"value with spaces\"}",
        "Path: C:\\Users\\Test Folder\\file.txt",
        "URL: https://example.com/path with spaces?param=value with spaces",
    ]
    
    for i, content in enumerate(test_cases):
        print(f"\nTest case {i+1}: {repr(content[:50])}...")
        
        # Create clipboard command
        clipboard_cmd = ClipboardCommand.create(content)
        
        # Convert to legacy format
        legacy_string = clipboard_cmd.to_legacy_string()
        print(f"  Legacy: {repr(legacy_string)}")
        
        # Parse back using protocol adapter
        adapter = ProtocolAdapter()
        structured_back = adapter.legacy_to_structured(legacy_string, source="test", target="test")
        
        if structured_back:
            reconstructed_content = structured_back.payload.get("content", "")
            
            if reconstructed_content == content:
                print(f"  ✓ Test case {i+1} successful")
            else:
                print(f"  ✗ Test case {i+1} failed")
                print(f"    Expected: {repr(content)}")
                print(f"    Got: {repr(reconstructed_content)}")
        else:
            print(f"  ✗ Test case {i+1} failed - no structured message")


def test_chunking_edge_cases():
    """Test edge cases in clipboard chunking."""
    print("\n=== Chunking Edge Cases Test ===")
    
    edge_cases = [
        ("Empty", ""),
        ("Single char", "X"),
        ("Only newlines", "\n\n\n"),
        ("Only spaces", "   "),
        ("Mixed whitespace", " \t\n\r "),
        ("Unicode only", "🎉🚀☀️"),
        ("Large single word", "X" * 2000),
        ("Many small words", " ".join(["word"] * 500)),
    ]
    
    for name, content in edge_cases:
        print(f"\nTesting {name}: {len(content)} chars")
        
        # Make it large enough to force chunking if not already
        test_content = content + "Y" * max(0, 1500 - len(content))
        
        # Test complete flow
        clipboard_cmd = ClipboardCommand.create(test_content)
        protocol_msg = clipboard_cmd.to_protocol_message()
        
        # Chunk
        builder = MessageBuilder()
        chunks = builder.create_chunked_message(protocol_msg, 512)
        
        # Serialize and reconstruct
        chunk_manager = ChunkManager()
        transmitted_data = b''
        for chunk in chunks:
            transmitted_data += chunk.to_bytes()
        
        complete_messages, _ = chunk_manager.receive_data(transmitted_data)
        
        if complete_messages and isinstance(complete_messages[0], ProtocolMessage):
            reconstructed_content = complete_messages[0].payload.get("content", "")
            
            if reconstructed_content == test_content:
                print(f"  ✓ {name} successful ({len(chunks)} chunks)")
            else:
                print(f"  ✗ {name} failed")
        else:
            print(f"  ✗ {name} failed - no reconstruction")


def benchmark_performance():
    """Quick performance benchmark."""
    print("\n=== Performance Benchmark ===")
    
    sizes = [1000, 5000, 10000]
    
    for size in sizes:
        content = "Performance test: " + "X" * (size - 18)
        
        start_time = time.time()
        
        # Full flow
        clipboard_cmd = ClipboardCommand.create(content)
        protocol_msg = clipboard_cmd.to_protocol_message()
        
        chunk_manager = ChunkManager(chunk_size=1024)
        chunks = chunk_manager.message_builder.create_chunked_message(protocol_msg, 1024)
        
        transmitted_data = b''
        for chunk in chunks:
            transmitted_data += chunk.to_bytes()
        
        complete_messages, _ = chunk_manager.receive_data(transmitted_data)
        
        end_time = time.time()
        duration = (end_time - start_time) * 1000
        
        success = (complete_messages and 
                  complete_messages[0].payload.get("content") == content)
        
        print(f"Size: {size:5d}, Chunks: {len(chunks):2d}, Time: {duration:6.2f}ms, Success: {success}")


if __name__ == "__main__":
    test_clipboard_reconstruction_complete_flow()
    test_protocol_adapter_clipboard_parsing()
    test_chunking_edge_cases()
    benchmark_performance()