from typing import Optional
from utils.command.BaseCommand import BaseCommand
from utils.protocol.message import ProtocolMessage


class ClipboardCommand(BaseCommand):
    """
    Structured command for clipboard actions.
    """
    
    DESCRIPTION = "clipboard"
    
    def __init__(self, content: str, content_type: str = "text", **kwargs):
        super().__init__(**kwargs)
        self.content = content
        self.content_type = content_type
    
    @classmethod
    def create(cls, content: str, content_type: str = "text", **kwargs) -> 'ClipboardCommand':
        """Create a clipboard command."""
        return cls(content=content, content_type=content_type, **kwargs)
    
    def to_protocol_message(self, source: Optional[str] = None, 
                          target: Optional[str] = None) -> ProtocolMessage:
        """Convert to ProtocolMessage for transmission."""
        from utils.protocol.message import MessageBuilder
        
        builder = MessageBuilder()
        return builder.create_clipboard_message(
            content=self.content,
            content_type=self.content_type,
            source=source,
            target=target or self.screen
        )
    
    def to_legacy_string(self) -> str:
        """Convert to legacy format_command string."""
        # Legacy clipboard commands usually just contain content
        return f"clipboard {self.content}"
    
    @classmethod
    def from_legacy_string(cls, command_str: str, **kwargs) -> Optional['ClipboardCommand']:
        """Parse from legacy format_command string."""
        if not command_str.startswith("clipboard "):
            return None
            
        # Extract content after "clipboard " prefix
        content = command_str[10:]  # len("clipboard ") = 10
        return cls.create(content=content, **kwargs)