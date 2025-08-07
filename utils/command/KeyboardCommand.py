from typing import Optional
from utils.Interfaces import IBaseCommand
from utils.protocol.message import ProtocolMessage


class KeyboardCommand(IBaseCommand):
    """
    Structured command for keyboard actions.
    Supports: press, release
    """
    
    DESCRIPTION = "keyboard"
    
    # Keyboard action types
    PRESS = "press"
    RELEASE = "release"
    
    def __init__(self, action: str, key: str, **kwargs):
        super().__init__(**kwargs)
        self.action = action
        self.key = key
    
    @classmethod
    def press(cls, key: str, **kwargs) -> 'KeyboardCommand':
        """Create a keyboard press command."""
        return cls(action=cls.PRESS, key=key, **kwargs)
    
    @classmethod
    def release(cls, key: str, **kwargs) -> 'KeyboardCommand':
        """Create a keyboard release command."""
        return cls(action=cls.RELEASE, key=key, **kwargs)
    
    def to_protocol_message(self, source: Optional[str] = None, 
                          target: Optional[str] = None) -> ProtocolMessage:
        """Convert to ProtocolMessage for transmission."""
        from utils.protocol.message import MessageBuilder
        
        builder = MessageBuilder()
        return builder.create_keyboard_message(
            key=self.key,
            event=self.action,
            source=source,
            target=target or self.screen
        )
    
    def to_legacy_string(self) -> str:
        """Convert to legacy format_command string."""
        return f"keyboard {self.action} {self.key}"
    
    @classmethod
    def from_legacy_string(cls, command_str: str, **kwargs) -> Optional['KeyboardCommand']:
        """Parse from legacy format_command string."""
        parts = command_str.split()
        if len(parts) < 3 or parts[0] != "keyboard":
            return None
            
        action = parts[1]
        key = parts[2]
        
        if action == cls.PRESS:
            return cls.press(key, **kwargs)
        elif action == cls.RELEASE:
            return cls.release(key, **kwargs)
            
        return None