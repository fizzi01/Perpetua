from typing import Optional
from utils.command.BaseCommand import BaseCommand
from utils.protocol.message import ProtocolMessage


class MouseCommand(BaseCommand):
    """
    Structured command for mouse actions.
    Supports: position, click, right_click, middle_click, scroll
    """
    
    DESCRIPTION = "mouse"
    
    # Mouse action types
    POSITION = "position"
    CLICK = "click"
    RIGHT_CLICK = "right_click"
    MIDDLE_CLICK = "middle_click"
    SCROLL = "scroll"
    
    def __init__(self, action: str, x: Optional[float] = None, y: Optional[float] = None,
                 is_pressed: Optional[bool] = None, dx: Optional[float] = None, 
                 dy: Optional[float] = None, **kwargs):
        super().__init__(**kwargs)
        self.action = action
        self.x = x
        self.y = y
        self.is_pressed = is_pressed
        self.dx = dx
        self.dy = dy
    
    @classmethod
    def position(cls, x: float, y: float, **kwargs) -> 'MouseCommand':
        """Create a mouse position command."""
        return cls(action=cls.POSITION, x=x, y=y, **kwargs)
    
    @classmethod
    def click(cls, x: float, y: float, is_pressed: bool, **kwargs) -> 'MouseCommand':
        """Create a mouse click command."""
        return cls(action=cls.CLICK, x=x, y=y, is_pressed=is_pressed, **kwargs)
    
    @classmethod
    def right_click(cls, x: float, y: float, **kwargs) -> 'MouseCommand':
        """Create a mouse right click command."""
        return cls(action=cls.RIGHT_CLICK, x=x, y=y, **kwargs)
    
    @classmethod
    def middle_click(cls, x: float, y: float, **kwargs) -> 'MouseCommand':
        """Create a mouse middle click command."""
        return cls(action=cls.MIDDLE_CLICK, x=x, y=y, **kwargs)
    
    @classmethod
    def scroll(cls, dx: float, dy: float, **kwargs) -> 'MouseCommand':
        """Create a mouse scroll command."""
        return cls(action=cls.SCROLL, dx=dx, dy=dy, **kwargs)
    
    def to_protocol_message(self, source: Optional[str] = None, 
                          target: Optional[str] = None) -> ProtocolMessage:
        """Convert to ProtocolMessage for transmission."""
        from utils.protocol.message import MessageBuilder
        
        builder = MessageBuilder()
        return builder.create_mouse_message(
            x=self.x or 0,
            y=self.y or 0,
            event=self.action,
            is_pressed=self.is_pressed or False,
            source=source,
            target=target or self.screen
        )
    
    def to_legacy_string(self) -> str:
        """Convert to legacy format_command string."""
        if self.action == self.POSITION:
            return f"mouse position {self.x} {self.y}"
        elif self.action == self.CLICK:
            return f"mouse click {self.x} {self.y} {str(self.is_pressed).lower()}"
        elif self.action == self.RIGHT_CLICK:
            return f"mouse right_click {self.x} {self.y}"
        elif self.action == self.MIDDLE_CLICK:
            return f"mouse middle_click {self.x} {self.y}"
        elif self.action == self.SCROLL:
            return f"mouse scroll {self.dx} {self.dy}"
        return ""
    
    @classmethod
    def from_legacy_string(cls, command_str: str, **kwargs) -> Optional['MouseCommand']:
        """Parse from legacy format_command string."""
        parts = command_str.split()
        if len(parts) < 2 or parts[0] != "mouse":
            return None
            
        action = parts[1]
        
        if action == "position" and len(parts) >= 4:
            return cls.position(float(parts[2]), float(parts[3]), **kwargs)
        elif action == "click" and len(parts) >= 5:
            return cls.click(float(parts[2]), float(parts[3]), 
                           parts[4].lower() == "true", **kwargs)
        elif action == "right_click" and len(parts) >= 4:
            return cls.right_click(float(parts[2]), float(parts[3]), **kwargs)
        elif action == "middle_click" and len(parts) >= 4:
            return cls.middle_click(float(parts[2]), float(parts[3]), **kwargs)
        elif action == "scroll" and len(parts) >= 4:
            return cls.scroll(float(parts[2]), float(parts[3]), **kwargs)
            
        return None