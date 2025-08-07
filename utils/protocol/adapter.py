"""
Protocol adapter for handling both new structured and legacy message formats.
"""
import json
from typing import Union, Optional, Tuple, List
from .message import ProtocolMessage, MessageBuilder
from .chunking import ChunkReassembler
from utils.net.netData import extract_command_parts, format_command


class ProtocolAdapter:
    """
    Adapter that handles conversion between legacy string format and new structured format.
    Provides backward compatibility while enabling new protocol features.
    """
    
    # Protocol version markers
    PROTOCOL_V2_MARKER = "PYCONT_V2:"
    
    def __init__(self, chunk_size: int = 4096):
        self.message_builder = MessageBuilder()
        self.reassembler = ChunkReassembler()
    
    def is_structured_message(self, data: str) -> bool:
        """Check if message uses new structured format."""
        return data.startswith(self.PROTOCOL_V2_MARKER)
    
    def encode_structured_message(self, message: ProtocolMessage) -> str:
        """Encode structured message for transmission."""
        return f"{self.PROTOCOL_V2_MARKER}{message.to_json()}"
    
    def decode_structured_message(self, data: str) -> ProtocolMessage:
        """Decode structured message from transmission format."""
        if not self.is_structured_message(data):
            raise ValueError("Not a structured message")
        
        json_data = data[len(self.PROTOCOL_V2_MARKER):]
        return ProtocolMessage.from_json(json_data)
    
    def legacy_to_structured(self, legacy_command: str, source: str = None, 
                           target: str = None) -> Optional[ProtocolMessage]:
        """
        Convert legacy string command to structured message format.
        
        Args:
            legacy_command: Legacy command string (e.g., "mouse::move::100::200")
            source: Source identifier
            target: Target identifier
            
        Returns:
            ProtocolMessage or None if conversion fails
        """
        try:
            parts = extract_command_parts(legacy_command)
            if not parts:
                return None
            
            command_type = parts[0]
            
            if command_type == "mouse" and len(parts) >= 4:
                # mouse::event::x::y::pressed
                event = parts[1]
                x = float(parts[2])
                y = float(parts[3])
                is_pressed = parts[4] == "true" if len(parts) > 4 else False
                
                return self.message_builder.create_mouse_message(
                    x=x, y=y, event=event, is_pressed=is_pressed,
                    source=source, target=target
                )
            
            elif command_type == "keyboard" and len(parts) >= 3:
                # keyboard::key::event
                key = parts[1]
                event = parts[2]
                
                return self.message_builder.create_keyboard_message(
                    key=key, event=event, source=source, target=target
                )
            
            elif command_type == "clipboard" and len(parts) >= 2:
                # clipboard::content
                content = parts[1]
                content_type = parts[2] if len(parts) > 2 else "text"
                
                return self.message_builder.create_clipboard_message(
                    content=content, content_type=content_type,
                    source=source, target=target
                )
            
            elif command_type.startswith("file_"):
                # file_start::filename::size, file_chunk::data::index, etc.
                file_command = command_type[5:]  # Remove "file_" prefix
                data = {}
                
                if file_command == "start" and len(parts) >= 3:
                    data = {"filename": parts[1], "size": int(parts[2])}
                elif file_command == "chunk" and len(parts) >= 3:
                    data = {"data": parts[1], "index": int(parts[2])}
                elif file_command == "end" and len(parts) >= 2:
                    data = {"filename": parts[1]}
                
                return self.message_builder.create_file_message(
                    command=file_command, data=data, source=source, target=target
                )
            
            elif command_type == "return" and len(parts) >= 2:
                # return::command::data
                screen_command = parts[1]
                screen_data = {}
                if len(parts) > 2:
                    # Try to parse additional data
                    for i in range(2, len(parts), 2):
                        if i + 1 < len(parts):
                            screen_data[parts[i]] = parts[i + 1]
                
                return self.message_builder.create_screen_message(
                    command=screen_command, data=screen_data,
                    source=source, target=target
                )
            
            return None
            
        except (ValueError, IndexError) as e:
            return None
    
    def structured_to_legacy(self, message: ProtocolMessage) -> str:
        """
        Convert structured message back to legacy format for compatibility.
        
        Args:
            message: ProtocolMessage to convert
            
        Returns:
            Legacy command string
        """
        msg_type = message.message_type
        payload = message.payload

        if msg_type == "mouse":
            # Use MouseCommand to generate legacy format
            from utils.command.MouseCommand import MouseCommand

            event = payload.get("event", "move")
            x = payload.get("x", 0)
            y = payload.get("y", 0)
            is_pressed = payload.get("is_pressed", False)

            mouse_cmd = MouseCommand(action=event, x=x, y=y, dx=x, dy=y, is_pressed=is_pressed)
            return mouse_cmd.to_legacy_string()

        elif msg_type == "keyboard":
            # Use KeyboardCommand to generate legacy format
            from utils.command.KeyboardCommand import KeyboardCommand

            key = payload.get("key", "")
            event = payload.get("event", "")

            keyboard_cmd = KeyboardCommand(action=event, key=key)
            return keyboard_cmd.to_legacy_string()

        elif msg_type == "clipboard":
            # Use ClipboardCommand to generate legacy format
            from utils.command.ClipboardCommand import ClipboardCommand

            content = payload.get("content", "")
            content_type = payload.get("content_type", "text")

            clipboard_cmd = ClipboardCommand(content=content, content_type=content_type)
            return clipboard_cmd.to_legacy_string()
        elif msg_type == "file":
            # Convert to: file_command::data
            file_command = payload.get("command", "")
            
            if file_command == "start":
                filename = payload.get("filename", "")
                size = payload.get("size", 0)
                return format_command(f"file_start {filename} {size}")
            
            elif file_command == "chunk":
                data = payload.get("data", "")
                index = payload.get("index", 0)
                return format_command(f"file_chunk {data} {index}")
            
            elif file_command == "end":
                filename = payload.get("filename", "")
                return format_command(f"file_end {filename}")

        elif msg_type == "return":
            # Use ReturnCommand to generate legacy format
            from utils.command.ReturnCommand import ReturnCommand

            screen_command = payload.get("command", "")
            screen_data = payload.get("data", {})
            screen_value = screen_data.get("value", 0.0)

            return_cmd = ReturnCommand(direction=screen_command, value=screen_value)

            return return_cmd.to_legacy_string()

        return ""
    
    def encode_message(self, message: ProtocolMessage, use_structured: bool = True) -> str:
        """
        Encode message in appropriate format.
        
        Args:
            message: Message to encode
            use_structured: Whether to use new structured format
            
        Returns:
            Encoded message string
        """
        if use_structured:
            return self.encode_structured_message(message)
        else:
            return self.structured_to_legacy(message)
    
    def decode_message(self, data: str, source: str = None, 
                      target: str = None) -> Optional[ProtocolMessage]:
        """
        Decode message from either format.
        
        Args:
            data: Message data string
            source: Source identifier  
            target: Target identifier
            
        Returns:
            ProtocolMessage or None if decoding fails
        """
        if self.is_structured_message(data):
            try:
                return self.decode_structured_message(data)
            except (json.JSONDecodeError, ValueError):
                return None
        else:
            return self.legacy_to_structured(data, source, target)