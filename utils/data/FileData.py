"""
File data object representing file transfer operations.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from utils.protocol.message import ProtocolMessage
from .BaseDataObject import IDataObject


@dataclass
class FileData(IDataObject):
    """
    Data object representing file transfer operations.
    """
    
    command: str  # file_start, file_chunk, file_end, file_request, file_copied
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    chunk_data: Optional[str] = None
    chunk_index: Optional[int] = None
    total_chunks: Optional[int] = None
    file_path: Optional[str] = None
    error_message: Optional[str] = None
    source: Optional[str] = None
    target: Optional[str] = None
    
    @property
    def data_type(self) -> str:
        return "file"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert file data to dictionary representation."""
        data = {
            "command": self.command
        }
        
        # Add non-None fields
        fields = ["file_name", "file_size", "chunk_data", "chunk_index", 
                 "total_chunks", "file_path", "error_message", "source", "target"]
        
        for field in fields:
            value = getattr(self, field, None)
            if value is not None:
                data[field] = value
                
        return data
    
    @classmethod
    def from_protocol_message(cls, message: ProtocolMessage) -> 'FileData':
        """Create FileData from ProtocolMessage."""
        if message.message_type != "file":
            raise ValueError(f"Expected file message, got {message.message_type}")
        
        payload = message.payload
        
        return cls(
            command=payload.get("command", ""),
            file_name=payload.get("file_name"),
            file_size=payload.get("file_size"),
            chunk_data=payload.get("chunk_data"),
            chunk_index=payload.get("chunk_index"),
            total_chunks=payload.get("total_chunks"),
            file_path=payload.get("file_path"),
            error_message=payload.get("error_message"),
            source=message.source,
            target=message.target
        )
    
    def validate(self) -> bool:
        """Validate that the file data contains valid data."""
        # Check required fields
        if not isinstance(self.command, str) or not self.command:
            return False
            
        # Validate command types
        valid_commands = ["file_start", "file_chunk", "file_end", "file_request", "file_copied"]
        if self.command not in valid_commands:
            return False
        
        # Validate specific command requirements
        if self.command == "file_start":
            if not self.file_name or self.file_size is None:
                return False
                
        elif self.command == "file_chunk":
            if (self.chunk_data is None or self.chunk_index is None or 
                self.total_chunks is None):
                return False
                
        elif self.command == "file_request":
            if not self.file_name:
                return False
                
        return True
    
    def is_file_start(self) -> bool:
        """Check if this is a file start command."""
        return self.command == "file_start"
    
    def is_file_chunk(self) -> bool:
        """Check if this is a file chunk command."""
        return self.command == "file_chunk"
    
    def is_file_end(self) -> bool:
        """Check if this is a file end command."""
        return self.command == "file_end"
    
    def is_file_request(self) -> bool:
        """Check if this is a file request command."""
        return self.command == "file_request"
    
    def is_file_copied(self) -> bool:
        """Check if this is a file copied command."""
        return self.command == "file_copied"
    
    def get_chunk_progress(self) -> Optional[float]:
        """Get the progress of file transfer (0.0 to 1.0)."""
        if self.chunk_index is not None and self.total_chunks is not None and self.total_chunks > 0:
            return (self.chunk_index + 1) / self.total_chunks
        return None