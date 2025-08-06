"""
Structured message format for improved data handling and ordering.
"""
import json
import time
from typing import Dict, Any, Optional
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