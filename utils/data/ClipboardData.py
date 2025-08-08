"""
Clipboard data object representing clipboard content.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from utils.protocol.message import ProtocolMessage
from .BaseDataObject import IDataObject


@dataclass
class ClipboardData(IDataObject):
    """
    Data object representing clipboard content.
    """
    
    content: str
    content_type: str = "text"
    source: Optional[str] = None
    target: Optional[str] = None
    
    @property
    def data_type(self) -> str:
        return "clipboard"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert clipboard data to dictionary representation."""
        data = {
            "content": self.content,
            "content_type": self.content_type
        }
        
        if self.source is not None:
            data["source"] = self.source
        if self.target is not None:
            data["target"] = self.target
            
        return data
    
    @classmethod
    def from_protocol_message(cls, message: ProtocolMessage) -> 'ClipboardData':
        """Create ClipboardData from ProtocolMessage."""
        if message.message_type != "clipboard":
            raise ValueError(f"Expected clipboard message, got {message.message_type}")
        
        payload = message.payload
        
        return cls(
            content=payload.get("content", ""),
            content_type=payload.get("content_type", "text"),
            source=message.source,
            target=message.target
        )
    
    def validate(self) -> bool:
        """Validate that the clipboard data contains valid data."""
        # Check required fields
        if not isinstance(self.content, str):
            return False
        
        if not isinstance(self.content_type, str) or not self.content_type:
            return False
            
        # Validate content types
        valid_content_types = ["text", "html", "image", "file"]
        if self.content_type not in valid_content_types:
            return False
            
        return True
    
    def is_text(self) -> bool:
        """Check if this is text content."""
        return self.content_type == "text"
    
    def is_html(self) -> bool:
        """Check if this is HTML content."""
        return self.content_type == "html"
    
    def is_image(self) -> bool:
        """Check if this is image content."""
        return self.content_type == "image"
    
    def is_file(self) -> bool:
        """Check if this is file content."""
        return self.content_type == "file"
    
    def get_content_length(self) -> int:
        """Get the length of the content."""
        return len(self.content)