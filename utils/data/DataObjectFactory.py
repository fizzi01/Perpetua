"""
Factory for creating data objects from ProtocolMessages.
Provides centralized mapping from ProtocolMessage to appropriate DataObject types.
"""

from typing import Optional, Type
from utils.protocol.message import ProtocolMessage
from .BaseDataObject import IDataObject
from .MouseData import MouseData
from .KeyboardData import KeyboardData
from .ClipboardData import ClipboardData
from .FileData import FileData
from .ReturnData import ReturnData


class DataObjectFactory:
    """
    Factory class for creating DataObject instances from ProtocolMessage.
    Provides centralized mapping and conversion functionality.
    """
    
    # Registry mapping message types to DataObject classes
    _TYPE_REGISTRY = {
        "mouse": MouseData,
        "keyboard": KeyboardData,
        "clipboard": ClipboardData,
        "file": FileData,
        "return": ReturnData
    }
    
    @classmethod
    def create_from_protocol_message(cls, message: ProtocolMessage) -> Optional[IDataObject]:
        """
        Create appropriate DataObject from ProtocolMessage.
        
        Args:
            message: ProtocolMessage to convert
            
        Returns:
            DataObject instance or None if message type not supported
        """
        if not isinstance(message, ProtocolMessage):
            raise TypeError("Expected ProtocolMessage instance")
        
        message_type = message.message_type
        
        if message_type not in cls._TYPE_REGISTRY:
            return None
            
        data_class = cls._TYPE_REGISTRY[message_type]
        
        try:
            return data_class.from_protocol_message(message)
        except Exception as e:
            # Log error but don't crash
            print(f"Error creating {message_type} data object: {e}")
            return None
    
    @classmethod
    def get_supported_types(cls) -> list[str]:
        """Get list of supported message types."""
        return list(cls._TYPE_REGISTRY.keys())
    
    @classmethod
    def register_type(cls, message_type: str, data_class: Type[IDataObject]):
        """
        Register a new data object type.
        
        Args:
            message_type: The message type string
            data_class: The DataObject class to handle this type
        """
        if not issubclass(data_class, IDataObject):
            raise TypeError("data_class must implement IDataObject")
            
        cls._TYPE_REGISTRY[message_type] = data_class
    
    @classmethod
    def is_supported_type(cls, message_type: str) -> bool:
        """Check if a message type is supported."""
        return message_type in cls._TYPE_REGISTRY
    
    @classmethod
    def create_mouse_data(cls, x: float, y: float, event: str, is_pressed: bool = False, 
                         dx: Optional[float] = None, dy: Optional[float] = None,
                         source: Optional[str] = None, target: Optional[str] = None) -> MouseData:
        """Create MouseData directly."""
        return MouseData(
            x=x, y=y, event=event, is_pressed=is_pressed,
            dx=dx, dy=dy, source=source, target=target
        )
    
    @classmethod
    def create_keyboard_data(cls, key: str, event: str,
                            source: Optional[str] = None, target: Optional[str] = None) -> KeyboardData:
        """Create KeyboardData directly."""
        return KeyboardData(key=key, event=event, source=source, target=target)
    
    @classmethod
    def create_clipboard_data(cls, content: str, content_type: str = "text",
                             source: Optional[str] = None, target: Optional[str] = None) -> ClipboardData:
        """Create ClipboardData directly."""
        return ClipboardData(content=content, content_type=content_type, source=source, target=target)
    
    @classmethod
    def create_file_data(cls, command: str, **kwargs) -> FileData:
        """Create FileData directly."""
        return FileData(command=command, **kwargs)
    
    @classmethod
    def create_return_data(cls, command: str, value: float,
                          source: Optional[str] = None, target: Optional[str] = None) -> ReturnData:
        """Create ReturnData directly."""
        return ReturnData(command=command, value=value, source=source, target=target)