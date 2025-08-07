"""
Simplified base command class for structured commands.
"""

from typing import Optional, Any, Dict
from abc import ABC, abstractmethod


class BaseCommand(ABC):
    """
    Base class for all structured commands.
    Provides common interface without dataclass complexity.
    """
    
    DESCRIPTION = "base"
    
    def __init__(self, **kwargs):
        # Extract common fields
        self.context = kwargs.get('context', None)
        self.message_service = kwargs.get('message_service', None)
        self.event_bus = kwargs.get('event_bus', None)
        self.screen = kwargs.get('screen', None)
        self.payload = kwargs.get('payload', None)
    
    @abstractmethod
    def to_protocol_message(self, source: Optional[str] = None, 
                          target: Optional[str] = None):
        """Convert to ProtocolMessage for transmission."""
        pass
    
    @abstractmethod
    def to_legacy_string(self) -> str:
        """Convert to legacy format_command string."""
        pass
    
    @classmethod
    @abstractmethod
    def from_legacy_string(cls, command_str: str, **kwargs):
        """Parse from legacy format_command string."""
        pass
    
    def execute(self):
        """Execute the command - can be overridden by subclasses."""
        pass
    
    def __repr__(self):
        return f"{self.__class__.__name__}({self.DESCRIPTION})"
    
    def __str__(self):
        return self.to_legacy_string()