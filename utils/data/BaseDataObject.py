"""
Base interface for all data objects.
Data objects represent structured data extracted from ProtocolMessages.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from utils.protocol.message import ProtocolMessage


class IDataObject(ABC):
    """
    Base interface for all data objects.
    Data objects represent the actual objects received through protocol messages.
    """
    
    @property
    @abstractmethod
    def data_type(self) -> str:
        """Return the type of this data object (mouse, keyboard, clipboard, file, return)."""
        pass
    
    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Convert data object to dictionary representation."""
        pass
    
    @classmethod
    @abstractmethod
    def from_protocol_message(cls, message: ProtocolMessage) -> 'IDataObject':
        """Create data object from ProtocolMessage."""
        pass
    
    @abstractmethod
    def validate(self) -> bool:
        """Validate that the data object contains valid data."""
        pass
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.data_type})"