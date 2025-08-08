"""
Return data object representing return/screen commands.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from utils.protocol.message import ProtocolMessage
from .BaseDataObject import IDataObject


@dataclass
class ReturnData(IDataObject):
    """
    Data object representing return/screen commands.
    """
    
    command: str  # left, right, up, down
    value: float
    source: Optional[str] = None
    target: Optional[str] = None
    
    @property
    def data_type(self) -> str:
        return "return"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert return data to dictionary representation."""
        data = {
            "command": self.command,
            "value": self.value
        }
        
        if self.source is not None:
            data["source"] = self.source
        if self.target is not None:
            data["target"] = self.target
            
        return data
    
    @classmethod
    def from_protocol_message(cls, message: ProtocolMessage) -> 'ReturnData':
        """Create ReturnData from ProtocolMessage."""
        if message.message_type != "return":
            raise ValueError(f"Expected return message, got {message.message_type}")
        
        payload = message.payload
        
        return cls(
            command=payload.get("command", ""),
            value=float(payload.get("value", 0)),
            source=message.source,
            target=message.target
        )
    
    def validate(self) -> bool:
        """Validate that the return data contains valid data."""
        # Check required fields
        if not isinstance(self.command, str) or not self.command:
            return False
        
        if not isinstance(self.value, (int, float)):
            return False
            
        # Validate command types
        valid_commands = ["left", "right", "up", "down"]
        if self.command not in valid_commands:
            return False
            
        return True
    
    def is_horizontal(self) -> bool:
        """Check if this is a horizontal return command."""
        return self.command in ["left", "right"]
    
    def is_vertical(self) -> bool:
        """Check if this is a vertical return command."""
        return self.command in ["up", "down"]
    
    def is_left(self) -> bool:
        """Check if this is a left return command."""
        return self.command == "left"
    
    def is_right(self) -> bool:
        """Check if this is a right return command."""
        return self.command == "right"
    
    def is_up(self) -> bool:
        """Check if this is an up return command."""
        return self.command == "up"
    
    def is_down(self) -> bool:
        """Check if this is a down return command."""
        return self.command == "down"