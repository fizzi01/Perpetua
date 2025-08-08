"""
Keyboard data object representing keyboard events.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from utils.protocol.message import ProtocolMessage
from .BaseDataObject import IDataObject


@dataclass
class KeyboardData(IDataObject):
    """
    Data object representing keyboard events.
    """
    
    key: str
    event: str  # press, release
    source: Optional[str] = None
    target: Optional[str] = None
    
    @property
    def data_type(self) -> str:
        return "keyboard"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert keyboard data to dictionary representation."""
        data = {
            "key": self.key,
            "event": self.event
        }
        
        if self.source is not None:
            data["source"] = self.source
        if self.target is not None:
            data["target"] = self.target
            
        return data
    
    @classmethod
    def from_protocol_message(cls, message: ProtocolMessage) -> 'KeyboardData':
        """Create KeyboardData from ProtocolMessage."""
        if message.message_type != "keyboard":
            raise ValueError(f"Expected keyboard message, got {message.message_type}")
        
        payload = message.payload
        
        return cls(
            key=payload.get("key", ""),
            event=payload.get("event", "press"),
            source=message.source,
            target=message.target
        )
    
    def validate(self) -> bool:
        """Validate that the keyboard data contains valid data."""
        # Check required fields
        if not isinstance(self.key, str) or not self.key:
            return False
        
        if not isinstance(self.event, str) or not self.event:
            return False
            
        # Validate event types
        valid_events = ["press", "release"]
        if self.event not in valid_events:
            return False
            
        return True
    
    def is_press_event(self) -> bool:
        """Check if this is a key press event."""
        return self.event == "press"
    
    def is_release_event(self) -> bool:
        """Check if this is a key release event."""
        return self.event == "release"