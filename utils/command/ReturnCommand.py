from typing import Optional
from utils.Interfaces import IBaseCommand
from utils.protocol.message import ProtocolMessage


class ReturnCommand(IBaseCommand):
    """
    Structured command for screen return actions.
    Supports: left, right, up, down
    """
    
    DESCRIPTION = "return"
    
    # Return direction types
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"
    
    def __init__(self, direction: str, value: float, **kwargs):
        super().__init__(**kwargs)
        self.direction = direction
        self.value = value
    
    @classmethod
    def left(cls, value: float, **kwargs) -> 'ReturnCommand':
        """Create a return left command."""
        return cls(direction=cls.LEFT, value=value, **kwargs)
    
    @classmethod
    def right(cls, value: float, **kwargs) -> 'ReturnCommand':
        """Create a return right command."""
        return cls(direction=cls.RIGHT, value=value, **kwargs)
    
    @classmethod
    def up(cls, value: float, **kwargs) -> 'ReturnCommand':
        """Create a return up command."""
        return cls(direction=cls.UP, value=value, **kwargs)
    
    @classmethod
    def down(cls, value: float, **kwargs) -> 'ReturnCommand':
        """Create a return down command."""
        return cls(direction=cls.DOWN, value=value, **kwargs)
    
    def to_protocol_message(self, source: Optional[str] = None, 
                          target: Optional[str] = None) -> ProtocolMessage:
        """Convert to ProtocolMessage for transmission."""
        from utils.protocol.message import MessageBuilder
        
        builder = MessageBuilder()
        return builder.create_screen_message(
            command=f"{self.direction}",
            data={"value": self.value},
            source=source,
            target=target or self.screen
        )
    
    def to_legacy_string(self) -> str:
        """Convert to legacy format_command string."""
        return f"return {self.direction} {self.value}"
    
    @classmethod
    def from_legacy_string(cls, command_str: str, **kwargs) -> Optional['ReturnCommand']:
        """Parse from legacy format_command string."""
        parts = command_str.split()
        if len(parts) < 3 or parts[0] != "return":
            return None
            
        direction = parts[1]
        value = float(parts[2])
        
        if direction == cls.LEFT:
            return cls.left(value, **kwargs)
        elif direction == cls.RIGHT:
            return cls.right(value, **kwargs)
        elif direction == cls.UP:
            return cls.up(value, **kwargs)
        elif direction == cls.DOWN:
            return cls.down(value, **kwargs)
            
        return None