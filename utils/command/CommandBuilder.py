"""
Command Builder for creating structured commands easily.
Provides a unified interface for creating all command types.
"""

from typing import Optional, Union
from utils.command.BaseCommand import BaseCommand
from utils.command.MouseCommand import MouseCommand
from utils.command.KeyboardCommand import KeyboardCommand
from utils.command.ClipboardCommand import ClipboardCommand
from utils.command.ReturnCommand import ReturnCommand


class CommandBuilder:
    """
    Factory class for creating structured commands.
    Provides a clean interface for creating all types of commands.
    """
    
    @staticmethod
    def mouse_position(x: float, y: float, screen: Optional[str] = None) -> MouseCommand:
        """Create a mouse position command."""
        return MouseCommand.position(x=x, y=y, screen=screen)
    
    @staticmethod
    def mouse_click(x: float, y: float, is_pressed: bool, screen: Optional[str] = None) -> MouseCommand:
        """Create a mouse click command."""
        return MouseCommand.click(x=x, y=y, is_pressed=is_pressed, screen=screen)
    
    @staticmethod
    def mouse_right_click(x: float, y: float, screen: Optional[str] = None) -> MouseCommand:
        """Create a mouse right click command."""
        return MouseCommand.right_click(x=x, y=y, screen=screen)
    
    @staticmethod
    def mouse_middle_click(x: float, y: float, screen: Optional[str] = None) -> MouseCommand:
        """Create a mouse middle click command."""
        return MouseCommand.middle_click(x=x, y=y, screen=screen)
    
    @staticmethod
    def mouse_scroll(dx: float, dy: float, screen: Optional[str] = None) -> MouseCommand:
        """Create a mouse scroll command."""
        return MouseCommand.scroll(dx=dx, dy=dy, screen=screen)
    
    @staticmethod
    def keyboard_press(key: str, screen: Optional[str] = None) -> KeyboardCommand:
        """Create a keyboard press command."""
        return KeyboardCommand.press(key=key, screen=screen)
    
    @staticmethod
    def keyboard_release(key: str, screen: Optional[str] = None) -> KeyboardCommand:
        """Create a keyboard release command."""
        return KeyboardCommand.release(key=key, screen=screen)
    
    @staticmethod
    def clipboard(content: str, content_type: str = "text", screen: Optional[str] = None) -> ClipboardCommand:
        """Create a clipboard command."""
        return ClipboardCommand.create(content=content, content_type=content_type, screen=screen)
    
    @staticmethod
    def return_left(value: float, screen: Optional[str] = None) -> ReturnCommand:
        """Create a return left command."""
        return ReturnCommand.left(value=value, screen=screen)
    
    @staticmethod
    def return_right(value: float, screen: Optional[str] = None) -> ReturnCommand:
        """Create a return right command."""
        return ReturnCommand.right(value=value, screen=screen)
    
    @staticmethod
    def return_up(value: float, screen: Optional[str] = None) -> ReturnCommand:
        """Create a return up command."""
        return ReturnCommand.up(value=value, screen=screen)
    
    @staticmethod
    def return_down(value: float, screen: Optional[str] = None) -> ReturnCommand:
        """Create a return down command."""
        return ReturnCommand.down(value=value, screen=screen)
    
    @staticmethod
    def from_legacy_string(command_str: str, **kwargs) -> Optional[BaseCommand]:
        """
        Parse any command from legacy format_command string.
        
        Args:
            command_str: Legacy command string
            **kwargs: Additional context to pass to command
            
        Returns:
            Appropriate Command instance or None if parsing fails
        """
        command_str = command_str.strip()
        
        # Try to parse as different command types
        if command_str.startswith("mouse "):
            return MouseCommand.from_legacy_string(command_str, **kwargs)
        elif command_str.startswith("keyboard "):
            return KeyboardCommand.from_legacy_string(command_str, **kwargs)
        elif command_str.startswith("clipboard "):
            return ClipboardCommand.from_legacy_string(command_str, **kwargs)
        elif command_str.startswith("return "):
            return ReturnCommand.from_legacy_string(command_str, **kwargs)
        
        return None