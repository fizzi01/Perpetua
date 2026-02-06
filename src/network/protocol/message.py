"""
Structured message format for improved data handling and ordering.
"""


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

import base64
import struct
import time
import uuid
from typing import Dict, Any, Optional, List, ClassVar
from dataclasses import dataclass
import msgspec


# Messages type
@dataclass
class MessageType:
    MOUSE = "mouse"
    KEYBOARD = "keyboard"
    CLIPBOARD = "clipboard"
    FILE = "file"
    COMMAND = "command"
    SCREEN = "screen"
    EXCHANGE = "exchange"
    HEARTBEAT = "HEARTBEAT"


class ProtocolMessage(msgspec.Struct):
    """
    Standardized message format with timestamp and ordering support.
    """

    message_type: str  # mouse, keyboard, clipboard, file, screen
    timestamp: float
    sequence_id: int
    payload: Dict[str, Any]
    source: Optional[str] = None
    target: Optional[str] = None
    # Chunk metadata for protocol-level chunking
    message_id: Optional[str] = None
    chunk_index: Optional[int] = None
    total_chunks: Optional[int] = None
    is_chunk: bool = False

    _prefix_format: ClassVar[str] = "!Icc"
    prefix_lenght: ClassVar[int] = struct.calcsize(_prefix_format)

    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary for serialization."""
        return {f: getattr(self, f) for f in self.__struct_fields__}

    def to_json(self) -> str:
        """Serialize message to JSON string."""
        return msgspec.json.encode(self).decode("utf-8")

    def to_bytes(self) -> bytes:
        """
        Serialize message directly to binary format.
        This is more efficient than JSON for network transmission.
        """
        # Serialize to JSON bytes
        json_bytes = msgspec.json.encode(self)

        # Add length prefix for proper framing
        length = len(json_bytes)
        return struct.pack(self._prefix_format, length, b"P", b"Y") + json_bytes

    @classmethod
    def from_json(cls, json_str: str) -> "ProtocolMessage":
        """Deserialize message from JSON string."""
        return msgspec.json.decode(json_str.encode("utf-8"), type=cls)

    @classmethod
    def read_lenght_prefix(cls, data: bytes) -> int:
        """
        Read length prefix from binary data.

        Args:
            data: Binary data containing serialized ProtocolMessage
        """
        if len(data) < cls.prefix_lenght:
            raise ValueError("Invalid binary data: too short for length prefix")

        # Read length prefix
        length, p, y = struct.unpack(cls._prefix_format, data[: cls.prefix_lenght])
        if p != b"P" or y != b"Y":
            raise ValueError("Invalid binary data: not a protocol message")
        return length

    @classmethod
    def from_bytes(cls, data: bytes) -> "ProtocolMessage":
        """
        Deserialize message from binary format.

        Args:
            data: Binary data containing serialized ProtocolMessage

        Returns:
            Deserialized ProtocolMessage
        """
        if len(data) < cls.prefix_lenght:
            raise ValueError("Invalid binary data: too short for length prefix")

        # Read length prefix
        length, p, y = struct.unpack(cls._prefix_format, data[: cls.prefix_lenght])

        if p != b"P" or y != b"Y":
            raise ValueError("Invalid binary data: not a protocol message")

        if len(data) < cls.prefix_lenght + length:
            raise ValueError("Invalid binary data: incomplete message")

        # Extract JSON bytes
        json_bytes = data[cls.prefix_lenght : cls.prefix_lenght + length]

        # Parse JSON and create object
        return msgspec.json.decode(json_bytes, type=cls)

    def is_heartbeat(self) -> bool:
        """Check if the message is a heartbeat message."""
        return self.message_type == MessageType.HEARTBEAT

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProtocolMessage":
        """Create message from dictionary."""
        return msgspec.convert(data, type=cls)

    def get_serialized_size(self) -> int:
        """Get the size of the message when serialized to bytes."""
        return len(self.to_bytes())


class MessageBuilder:
    """
    Builder class for creating standardized protocol messages.
    """

    def __init__(self):
        self._sequence_counter = 0
        self._encoder = msgspec.json.Encoder()
        self._decoder = msgspec.json.Decoder()

    def _next_sequence_id(self) -> int:
        """Get next sequence ID for message ordering."""
        self._sequence_counter += 1
        return self._sequence_counter

    def _generate_message_id(self) -> str:
        """Generate unique message ID for chunk tracking."""
        return str(uuid.uuid4())

    def create_chunked_message(
        self, message: ProtocolMessage, max_chunk_size: int
    ) -> List[ProtocolMessage]:
        """
        Split a message into ProtocolMessage chunks with internal chunking metadata.
        Each chunk is a complete ProtocolMessage with chunking info in its fields.

        Args:
            message: Original message to chunk
            max_chunk_size: Maximum size for each serialized chunk in bytes

        Returns:
            List of ProtocolMessage chunks, each containing chunking metadata
        """
        # First check if message fits in one chunk
        if message.get_serialized_size() <= max_chunk_size:
            # No chunking needed, return original message
            return [message]

        # Need to split the payload into chunks
        payload_bytes = self._encoder.encode(message.payload)

        # Generate unique message ID for this chunking session
        message_id = self._generate_message_id()

        # Calculate chunk payload size
        # We need to account for the overhead of the ProtocolMessage structure
        # Create a sample chunk to estimate overhead
        sample_chunk = ProtocolMessage(
            message_type=message.message_type,
            timestamp=message.timestamp,
            sequence_id=self._next_sequence_id(),
            payload={},  # Empty payload
            source=message.source,
            target=message.target,
            message_id=message_id,
            chunk_index=0,
            total_chunks=1,
            is_chunk=True,
        )

        # Calculate overhead (everything except payload)
        overhead_size = sample_chunk.get_serialized_size()
        available_payload_size = (
            max_chunk_size - overhead_size - 50
        )  # 50 bytes safety margin

        # Adjust available payload size to account for Base64 expansion
        # Base64 expansion is approx 4/3. So available raw bytes is 3/4 of available string space.
        # But we simply chunk bytes and encode; the encoded string must fit.
        raw_chunk_size = (
            int(available_payload_size * 0.75) - 4
        )  # minus 4 for padding safety

        if raw_chunk_size <= 0:
            raise ValueError("Chunk size too small to fit ProtocolMessage overhead")

        # Split payload into chunks
        chunks = []
        total_chunks = (len(payload_bytes) + raw_chunk_size - 1) // raw_chunk_size

        for i in range(total_chunks):
            start_pos = i * raw_chunk_size
            end_pos = min(start_pos + raw_chunk_size, len(payload_bytes))
            chunk_payload_bytes = payload_bytes[start_pos:end_pos]

            # Encode chunk data to Base64 to ensure Safe transport as string
            chunk_data_b64 = base64.b64encode(chunk_payload_bytes).decode("ascii")

            # Create chunk ProtocolMessage
            chunk_message = ProtocolMessage(
                message_type=message.message_type,
                timestamp=message.timestamp,
                sequence_id=self._next_sequence_id(),
                payload={
                    "_chunk_data": chunk_data_b64,
                    "_original_type": message.message_type,
                },
                source=message.source,
                target=message.target,
                message_id=message_id,
                chunk_index=i,
                total_chunks=total_chunks,
                is_chunk=True,
            )
            chunks.append(chunk_message)

        return chunks

    @staticmethod
    def reconstruct_from_chunks(
        chunks: List[Optional[ProtocolMessage]],
    ) -> ProtocolMessage:
        """
        Reconstruct original message from ProtocolMessage chunks.

        Args:
            chunks: List of chunk ProtocolMessages with same message_id

        Returns:
            Reconstructed original ProtocolMessage
        """
        if not chunks:
            raise ValueError("No chunks provided")

        # Filter None
        valid_chunks: List[ProtocolMessage] = [c for c in chunks if c is not None]

        if not valid_chunks:
            raise ValueError("No valid chunks provided")

        if len(valid_chunks) == 1 and not valid_chunks[0].is_chunk:
            # Single message, no chunking
            return valid_chunks[0]

        # Sort chunks by index
        sorted_chunks = sorted(valid_chunks, key=lambda c: c.chunk_index)  # type: ignore

        # Verify chunk integrity
        first_chunk = sorted_chunks[0]
        expected_total = first_chunk.total_chunks

        if expected_total is None:
            raise ValueError("Chunk metadata missing")

        if len(sorted_chunks) != expected_total:
            raise ValueError(
                f"Missing chunks: expected {expected_total}, got {len(sorted_chunks)}"
            )

        # Reconstruct payload
        payload_bytes_list = []
        for chunk in sorted_chunks:
            if chunk.message_id != first_chunk.message_id:
                raise ValueError("Chunks have different message IDs")

            chunk_data = chunk.payload.get("_chunk_data", "")
            try:
                chunk_bytes = base64.b64decode(chunk_data)
                payload_bytes_list.append(chunk_bytes)
            except Exception:
                raise ValueError("Failed to decode chunk data")

        # Combine payload data
        combined_payload_bytes = b"".join(payload_bytes_list)
        combined_payload = msgspec.json.decode(combined_payload_bytes)

        # Create reconstructed message
        reconstructed = ProtocolMessage(
            message_type=first_chunk.payload.get(
                "_original_type", first_chunk.message_type
            ),
            timestamp=first_chunk.timestamp,
            sequence_id=first_chunk.sequence_id,
            payload=combined_payload,
            source=first_chunk.source,
            target=first_chunk.target,
            message_id=None,  # Clear chunking metadata
            chunk_index=None,
            total_chunks=None,
            is_chunk=False,
        )

        return reconstructed

    def create_chunk_from_data(
        self,
        data: str,
        chunk_index: int,
        total_chunks: int,
        message_id: str,
        original_type: str = "data",
    ) -> ProtocolMessage:
        """
        Create a chunk message from raw data.

        Args:
            data: Raw data for this chunk
            chunk_index: Index of this chunk
            total_chunks: Total number of chunks
            message_id: Unique message identifier
            original_type: Type of the original message

        Returns:
            ProtocolMessage representing the chunk
        """
        return ProtocolMessage(
            message_type="chunk",
            timestamp=time.time(),
            sequence_id=self._next_sequence_id(),
            payload={"data": data, "original_type": original_type},
            message_id=message_id,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            is_chunk=True,
        )

    def create_mouse_message(
        self,
        x: float = 0,
        y: float = 0,
        dx: float = 0,
        dy: float = 0,
        event: str = "",
        is_pressed: bool = False,
        source: Optional[str] = None,
        target: Optional[str] = None,
        **kwargs,
    ) -> ProtocolMessage:
        """Create a mouse event message with timestamp."""
        return ProtocolMessage(
            message_type=MessageType.MOUSE,
            timestamp=time.time(),
            sequence_id=self._next_sequence_id(),
            payload={
                "x": x,
                "y": y,
                "dx": dx,
                "dy": dy,
                "event": event,
                "is_pressed": is_pressed,
                **kwargs,
            },
            source=source,
            target=target,
        )

    def create_keyboard_message(
        self,
        key: str,
        event: str,
        source: Optional[str] = None,
        target: Optional[str] = None,
    ) -> ProtocolMessage:
        """Create a keyboard event message with timestamp."""
        return ProtocolMessage(
            message_type=MessageType.KEYBOARD,
            timestamp=time.time(),
            sequence_id=self._next_sequence_id(),
            payload={"key": key, "event": event},
            source=source,
            target=target,
        )

    def create_clipboard_message(
        self,
        content: str,
        content_type: str = "text",
        source: Optional[str] = None,
        target: Optional[str] = None,
    ) -> ProtocolMessage:
        """Create a clipboard message with timestamp."""
        return ProtocolMessage(
            message_type=MessageType.CLIPBOARD,
            timestamp=time.time(),
            sequence_id=self._next_sequence_id(),
            payload={"content": content, "content_type": content_type},
            source=source,
            target=target,
        )

    def create_screen_message(
        self,
        command: str,
        data: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
        target: Optional[str] = None,
    ) -> ProtocolMessage:
        """Create a screen notification message with timestamp."""
        return ProtocolMessage(
            message_type=MessageType.SCREEN,
            timestamp=time.time(),
            sequence_id=self._next_sequence_id(),
            payload={"command": command, "data": data or {}},
            source=source,
            target=target,
        )

    def create_command_message(
        self,
        command: str,
        params: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
        target: Optional[str] = None,
    ) -> ProtocolMessage:
        """Create a command message with timestamp."""
        return ProtocolMessage(
            message_type=MessageType.COMMAND,
            timestamp=time.time(),
            sequence_id=self._next_sequence_id(),
            payload={"command": command, "params": params or {}},
            source=source,
            target=target,
        )

    def create_file_message(
        self,
        command: str,
        data: Dict[str, Any],
        source: Optional[str] = None,
        target: Optional[str] = None,
    ) -> ProtocolMessage:
        """Create a file transfer message with timestamp."""
        return ProtocolMessage(
            message_type=MessageType.FILE,
            timestamp=time.time(),
            sequence_id=self._next_sequence_id(),
            payload={"command": command, **data},
            source=source,
            target=target,
        )

    def create_handshake_message(
        self,
        client_name: Optional[str],
        screen_resolution: Optional[str],
        screen_position: Optional[str] = None,
        additional_params: Optional[Dict[str, Any]] = None,
        ack: bool = True,
        ssl: bool = True,
        streams: Optional[List[int]] = None,
        source: Optional[str] = None,
        target: Optional[str] = None,
    ) -> ProtocolMessage:
        """Create a handshake message with timestamp."""
        return ProtocolMessage(
            message_type=MessageType.EXCHANGE,
            timestamp=time.time(),
            sequence_id=self._next_sequence_id(),
            payload={
                "client_name": client_name,
                "screen_resolution": screen_resolution,
                "screen_position": screen_position,
                "ack": ack,
                "ssl": ssl,
                "streams": streams or [],
                "additional_params": additional_params or {},
            },
            source=source,
            target=target,
        )
