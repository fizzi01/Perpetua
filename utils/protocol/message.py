"""
Structured message format for improved data handling and ordering.
"""
import json
import time
import uuid
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict


@dataclass
class ProtocolMessage:
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
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary for serialization."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Serialize message to JSON string."""
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_json(cls, json_str: str) -> 'ProtocolMessage':
        """Deserialize message from JSON string."""
        data = json.loads(json_str)
        return cls(**data)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProtocolMessage':
        """Create message from dictionary."""
        return cls(**data)


class MessageBuilder:
    """
    Builder class for creating standardized protocol messages.
    """
    
    def __init__(self):
        self._sequence_counter = 0
    
    def _next_sequence_id(self) -> int:
        """Get next sequence ID for message ordering."""
        self._sequence_counter += 1
        return self._sequence_counter
    
    def _generate_message_id(self) -> str:
        """Generate unique message ID for chunk tracking."""
        return str(uuid.uuid4())
    
    def create_chunked_message(self, message: ProtocolMessage, chunk_size: int) -> List[ProtocolMessage]:
        """
        Split a message into chunks for protocol-level chunking.
        
        Args:
            message: Original message to chunk
            chunk_size: Size of each chunk in bytes
            
        Returns:
            List of chunked protocol messages
        """
        # Serialize the original message
        message_json = message.to_json()
        message_bytes = message_json.encode('utf-8')
        
        # If message fits in one chunk, return as-is
        if len(message_bytes) <= chunk_size:
            return [message]
        
        # Create chunks
        chunks = []
        message_id = self._generate_message_id()
        total_chunks = (len(message_bytes) + chunk_size - 1) // chunk_size
        
        for i in range(total_chunks):
            start_pos = i * chunk_size
            end_pos = min(start_pos + chunk_size, len(message_bytes))
            chunk_data = message_bytes[start_pos:end_pos]
            
            # Create chunk message
            chunk_message = ProtocolMessage(
                message_type="chunk",
                timestamp=message.timestamp,
                sequence_id=self._next_sequence_id(),
                payload={
                    "data": chunk_data.decode('utf-8', errors='replace'),
                    "original_type": message.message_type
                },
                source=message.source,
                target=message.target,
                message_id=message_id,
                chunk_index=i,
                total_chunks=total_chunks,
                is_chunk=True
            )
            chunks.append(chunk_message)
        
        return chunks
    
    def create_chunk_from_data(self, data: str, chunk_index: int, total_chunks: int, 
                              message_id: str, original_type: str = "data") -> ProtocolMessage:
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
            payload={
                "data": data,
                "original_type": original_type
            },
            message_id=message_id,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            is_chunk=True
        )
    
    def create_mouse_message(self, x: float, y: float, event: str, 
                           is_pressed: bool = False, source: str = None, 
                           target: str = None) -> ProtocolMessage:
        """Create a mouse event message with timestamp."""
        return ProtocolMessage(
            message_type="mouse",
            timestamp=time.time(),
            sequence_id=self._next_sequence_id(),
            payload={
                "x": x,
                "y": y,
                "event": event,
                "is_pressed": is_pressed
            },
            source=source,
            target=target
        )
    
    def create_keyboard_message(self, key: str, event: str, 
                              source: str = None, target: str = None) -> ProtocolMessage:
        """Create a keyboard event message with timestamp."""
        return ProtocolMessage(
            message_type="keyboard",
            timestamp=time.time(),
            sequence_id=self._next_sequence_id(),
            payload={
                "key": key,
                "event": event
            },
            source=source,
            target=target
        )
    
    def create_clipboard_message(self, content: str, content_type: str = "text",
                               source: str = None, target: str = None) -> ProtocolMessage:
        """Create a clipboard message with timestamp."""
        return ProtocolMessage(
            message_type="clipboard",
            timestamp=time.time(),
            sequence_id=self._next_sequence_id(),
            payload={
                "content": content,
                "content_type": content_type
            },
            source=source,
            target=target
        )
    
    def create_screen_message(self, command: str, data: Dict[str, Any] = None,
                            source: str = None, target: str = None) -> ProtocolMessage:
        """Create a screen notification message with timestamp."""
        return ProtocolMessage(
            message_type="screen",
            timestamp=time.time(),
            sequence_id=self._next_sequence_id(),
            payload={
                "command": command,
                "data": data or {}
            },
            source=source,
            target=target
        )
    
    def create_file_message(self, command: str, data: Dict[str, Any],
                          source: str = None, target: str = None) -> ProtocolMessage:
        """Create a file transfer message with timestamp."""
        return ProtocolMessage(
            message_type="file",
            timestamp=time.time(),
            sequence_id=self._next_sequence_id(),
            payload={
                "command": command,
                **data
            },
            source=source,
            target=target
        )