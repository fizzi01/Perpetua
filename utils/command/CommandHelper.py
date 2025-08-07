"""
Helper utilities for transitioning from format_command to structured commands.
"""

from typing import Optional
from utils.command import CommandBuilder, BaseCommand
from utils.net.netData import format_command


class CommandHelper:
    """
    Helper class to ease the transition from format_command to structured commands.
    Provides methods that match the old format_command patterns but return structured commands.
    """
    
    @staticmethod
    def send_structured_command(send_function, command: BaseCommand, target: Optional[str] = None):
        """
        Send a structured command using the provided send function.
        
        Args:
            send_function: The sending function (e.g., self.send)
            command: The structured command to send
            target: Target screen/destination
        """
        # For now, convert to legacy format for compatibility
        # Later this can be updated to send ProtocolMessages directly
        legacy_string = command.to_legacy_string()
        formatted_command = format_command(legacy_string)
        send_function(target, formatted_command)
    
    @staticmethod
    def mouse_position(x: float, y: float) -> str:
        """Create a mouse position command string (legacy compatibility)."""
        cmd = CommandBuilder.mouse_position(x, y)
        return format_command(cmd.to_legacy_string())
    
    @staticmethod
    def mouse_click(x: float, y: float, is_pressed: bool) -> str:
        """Create a mouse click command string (legacy compatibility)."""
        cmd = CommandBuilder.mouse_click(x, y, is_pressed)
        return format_command(cmd.to_legacy_string())
    
    @staticmethod
    def mouse_right_click(x: float, y: float) -> str:
        """Create a mouse right click command string (legacy compatibility)."""
        cmd = CommandBuilder.mouse_right_click(x, y)
        return format_command(cmd.to_legacy_string())
    
    @staticmethod
    def mouse_middle_click(x: float, y: float) -> str:
        """Create a mouse middle click command string (legacy compatibility)."""
        cmd = CommandBuilder.mouse_middle_click(x, y)
        return format_command(cmd.to_legacy_string())
    
    @staticmethod
    def mouse_scroll(dx: float, dy: float) -> str:
        """Create a mouse scroll command string (legacy compatibility)."""
        cmd = CommandBuilder.mouse_scroll(dx, dy)
        return format_command(cmd.to_legacy_string())
    
    @staticmethod
    def keyboard_press(key: str) -> str:
        """Create a keyboard press command string (legacy compatibility)."""
        cmd = CommandBuilder.keyboard_press(key)
        return format_command(cmd.to_legacy_string())
    
    @staticmethod
    def keyboard_release(key: str) -> str:
        """Create a keyboard release command string (legacy compatibility)."""
        cmd = CommandBuilder.keyboard_release(key)
        return format_command(cmd.to_legacy_string())
    
    @staticmethod
    def clipboard(content: str) -> str:
        """Create a clipboard command string (legacy compatibility)."""
        cmd = CommandBuilder.clipboard(content)
        return format_command(cmd.to_legacy_string())
    
    @staticmethod
    def return_direction(direction: str, value: float) -> str:
        """Create a return command string (legacy compatibility)."""
        if direction == "left":
            cmd = CommandBuilder.return_left(value)
        elif direction == "right":
            cmd = CommandBuilder.return_right(value)
        elif direction == "up":
            cmd = CommandBuilder.return_up(value)
        elif direction == "down":
            cmd = CommandBuilder.return_down(value)
        else:
            raise ValueError(f"Invalid return direction: {direction}")
        
        return format_command(cmd.to_legacy_string())