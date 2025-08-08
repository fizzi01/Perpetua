"""
Mouse data object representing mouse events and positions.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from utils.protocol.message import ProtocolMessage
from .BaseDataObject import IDataObject


@dataclass
class MouseData(IDataObject):
    """
    Data object representing mouse events and positions.
    """
    
    x: float
    y: float
    event: str  # position, click, right_click, middle_click, scroll
    is_pressed: bool = False
    dx: Optional[float] = None  # For scroll events
    dy: Optional[float] = None  # For scroll events
    source: Optional[str] = None
    target: Optional[str] = None
    
    @property
    def data_type(self) -> str:
        return "mouse"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert mouse data to dictionary representation."""
        data = {
            "x": self.x,
            "y": self.y,
            "event": self.event,
            "is_pressed": self.is_pressed
        }
        
        if self.dx is not None:
            data["dx"] = self.dx
        if self.dy is not None:
            data["dy"] = self.dy
        if self.source is not None:
            data["source"] = self.source
        if self.target is not None:
            data["target"] = self.target
            
        return data
    
    @classmethod
    def from_protocol_message(cls, message: ProtocolMessage) -> 'MouseData':
        """Create MouseData from ProtocolMessage."""
        if message.message_type != "mouse":
            raise ValueError(f"Expected mouse message, got {message.message_type}")
        
        payload = message.payload
        
        return cls(
            x=float(payload.get("x", 0)),
            y=float(payload.get("y", 0)),
            event=payload.get("event", "position"),
            is_pressed=payload.get("is_pressed", False),
            dx=payload.get("dx"),
            dy=payload.get("dy"),
            source=message.source,
            target=message.target
        )
    
    def validate(self) -> bool:
        """Validate that the mouse data contains valid data."""
        # Check required fields
        if not isinstance(self.x, (int, float)) or not isinstance(self.y, (int, float)):
            return False
        
        if not isinstance(self.event, str) or not self.event:
            return False
            
        # Validate event types
        valid_events = ["position", "click", "right_click", "middle_click", "scroll", "release"]
        if self.event not in valid_events:
            return False
            
        # For scroll events, dx and dy should be provided
        if self.event == "scroll":
            if self.dx is None and self.dy is None:
                return False
                
        return True
    
    def is_click_event(self) -> bool:
        """Check if this is a click event."""
        return self.event in ["click", "right_click", "middle_click"]
    
    def is_scroll_event(self) -> bool:
        """Check if this is a scroll event."""
        return self.event == "scroll"
    
    def is_position_event(self) -> bool:
        """Check if this is a position event."""
        return self.event == "position"