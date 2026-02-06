#  Perpetua - open-source and cross-platform KVM software.
#  Copyright (c) 2026 Federico Izzi.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

import pytest
import struct
import base64
import time
import msgspec
from network.protocol.message import ProtocolMessage, MessageType, MessageBuilder


# Helper to create a basic message for testing
def create_test_message(payload_size=10):
    return ProtocolMessage(
        message_type=MessageType.MOUSE,
        timestamp=time.time(),
        sequence_id=1,
        payload={"data": "x" * payload_size},
        source="client",
        target="server",
    )


class TestProtocolMessage:
    def test_serialization_cycle(self):
        """Test full serialization cycle: Object -> JSON -> Object"""
        msg = create_test_message()

        # Test JSON serialization
        json_str = msg.to_json()
        assert isinstance(json_str, str)

        # Test JSON deserialization
        restored_msg = ProtocolMessage.from_json(json_str)
        assert restored_msg.message_type == msg.message_type
        assert restored_msg.sequence_id == msg.sequence_id
        assert restored_msg.payload == msg.payload
        assert restored_msg.timestamp == msg.timestamp

    def test_binary_serialization(self):
        """Test binary serialization including length prefix"""
        msg = create_test_message()

        binary_data = msg.to_bytes()
        assert isinstance(binary_data, bytes)

        # Verify prefix
        prefix_len = ProtocolMessage.prefix_lenght
        length, p, y = struct.unpack(
            ProtocolMessage._prefix_format, binary_data[:prefix_len]
        )

        assert p == b"P"
        assert y == b"Y"
        assert length == len(binary_data) - prefix_len

        # Verify body is valid JSON/msgspec
        json_body = binary_data[prefix_len:]
        decoded_map = msgspec.json.decode(json_body)
        assert decoded_map["message_type"] == msg.message_type

    def test_from_bytes(self):
        """Test deserialization from bytes with prefix"""
        msg = create_test_message()
        binary_data = msg.to_bytes()

        restored_msg = ProtocolMessage.from_bytes(binary_data)
        assert restored_msg.sequence_id == msg.sequence_id
        assert restored_msg.payload == msg.payload

    def test_read_length_prefix(self):
        """Test reading the length prefix separately"""
        msg = create_test_message()
        binary_data = msg.to_bytes()

        length = ProtocolMessage.read_lenght_prefix(binary_data)
        expected_length = len(binary_data) - ProtocolMessage.prefix_lenght
        assert length == expected_length

    def test_invalid_binary_data(self):
        """Test error handling for invalid binary data"""
        valid_msg = create_test_message().to_bytes()

        # Too short
        with pytest.raises(ValueError, match="too short"):
            ProtocolMessage.from_bytes(valid_msg[:2])

        # Invalid magic bytes
        invalid_prefix = struct.pack("!Icc", 10, b"X", b"Y") + b"{}"
        with pytest.raises(ValueError, match="not a protocol message"):
            ProtocolMessage.from_bytes(invalid_prefix)

        # Incomplete message
        length = len(valid_msg) - ProtocolMessage.prefix_lenght
        # Lie about length (say it is longer than it is)
        fake_len = length + 50
        fake_header = struct.pack(ProtocolMessage._prefix_format, fake_len, b"P", b"Y")
        incomplete_data = fake_header + valid_msg[ProtocolMessage.prefix_lenght :]

        with pytest.raises(ValueError, match="incomplete message"):
            ProtocolMessage.from_bytes(incomplete_data)


class TestMessageBuilder:
    def setup_method(self):
        self.builder = MessageBuilder()

    def test_create_helpers(self):
        """Test helper methods for creating specific message types"""
        # Mouse
        mouse_msg = self.builder.create_mouse_message(x=100, y=200, event="move")
        assert mouse_msg.message_type == MessageType.MOUSE
        assert mouse_msg.payload["x"] == 100
        assert mouse_msg.sequence_id == 1

        # Keyboard
        key_msg = self.builder.create_keyboard_message(key="space", event="down")
        assert key_msg.message_type == MessageType.KEYBOARD
        assert key_msg.payload["key"] == "space"
        assert key_msg.sequence_id == 2  # Sequence increments

        # Handshake
        handshake_msg = self.builder.create_handshake_message("client1", "1920x1080")
        assert handshake_msg.message_type == MessageType.EXCHANGE
        assert handshake_msg.payload["client_name"] == "client1"

    def test_chunking_small_message(self):
        """Test that small messages are not chunked"""
        msg = create_test_message(payload_size=10)
        # Assuming 1000 bytes is enough for overhead + 10 bytes payload
        chunks = self.builder.create_chunked_message(msg, max_chunk_size=1000)

        assert len(chunks) == 1
        assert not chunks[0].is_chunk
        assert chunks[0].message_type == msg.message_type

    def test_chunking_large_message(self):
        """Test splitting a large message into chunks"""
        # Create a large payload
        payload_data = "x" * 5000
        msg = ProtocolMessage(
            message_type=MessageType.FILE,
            timestamp=time.time(),
            sequence_id=1,
            payload={"content": payload_data},
            source="src",
            target="dst",
        )

        # Force small chunk size to ensure splitting
        # Rough estimate: overhead is ~200 bytes, so 1000 byte limit should split 5000 byte payload
        chunks = self.builder.create_chunked_message(msg, max_chunk_size=1000)

        assert len(chunks) > 1

        for i, chunk in enumerate(chunks):
            assert chunk.is_chunk
            assert chunk.chunk_index == i
            assert chunk.total_chunks == len(chunks)
            assert chunk.message_id is not None
            assert chunk.payload["_original_type"] == MessageType.FILE
            assert "_chunk_data" in chunk.payload

    def test_chunk_reconstruction(self):
        """Test reconstructing a message from chunks"""
        original_payload = {"key": "val" * 500}
        msg = ProtocolMessage(
            message_type=MessageType.COMMAND,
            timestamp=time.time(),
            sequence_id=10,
            payload=original_payload,
            source="A",
            target="B",
        )

        chunks = self.builder.create_chunked_message(msg, max_chunk_size=500)
        assert len(chunks) > 1

        reconstructed = self.builder.reconstruct_from_chunks(chunks)

        assert not reconstructed.is_chunk
        assert reconstructed.message_type == MessageType.COMMAND
        assert reconstructed.payload == original_payload
        assert reconstructed.source == "A"
        assert reconstructed.target == "B"

    def test_reconstruction_out_of_order(self):
        """Test reconstruction tolerates out-of-order chunks"""
        msg = create_test_message(payload_size=2000)
        chunks = self.builder.create_chunked_message(msg, max_chunk_size=500)

        # Shuffle chunks
        shuffled = chunks[::-1]
        reconstructed = self.builder.reconstruct_from_chunks(shuffled)

        assert reconstructed.payload == msg.payload

    def test_reconstruction_missing_chunks(self):
        """Test error when chunks are missing"""
        msg = create_test_message(payload_size=2000)
        chunks = self.builder.create_chunked_message(msg, max_chunk_size=500)

        # Remove one chunk
        incomplete = chunks[:-1]

        with pytest.raises(ValueError, match="Missing chunks"):
            self.builder.reconstruct_from_chunks(incomplete)

    def test_reconstruction_mixed_ids(self):
        """Test error when chunks have different message IDs"""
        msg1 = create_test_message(payload_size=2000)
        msg2 = create_test_message(payload_size=2000)

        chunks1 = self.builder.create_chunked_message(msg1, max_chunk_size=500)
        chunks2 = self.builder.create_chunked_message(msg2, max_chunk_size=500)

        # Make a mixed list that passes length checks but fails ID checks
        # chunks1 and chunks2 should have same length and structure
        assert len(chunks1) == len(chunks2)
        assert len(chunks1) >= 2

        # Start with all chunks from msg1
        mixed = list(chunks1)
        # Swap the second chunk with the second chunk from msg2
        mixed[1] = chunks2[1]

        with pytest.raises(ValueError, match="Chunks have different message IDs"):
            self.builder.reconstruct_from_chunks(mixed)
