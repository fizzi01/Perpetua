"""
Protocol-level chunking system without visible delimiters.
Handles message splitting and reassembly at the protocol layer.
"""
import threading
import time
import uuid
from typing import Dict, List, Optional, Union
from .message import ProtocolMessage, MessageBuilder


class ChunkReassembler:
    """
    Handles reassembly of chunked messages at the protocol level.
    Manages incomplete messages and provides timeout handling.
    """
    
    def __init__(self, timeout: float = 30.0):
        """
        Initialize chunk reassembler.
        
        Args:
            timeout: Timeout in seconds for incomplete messages
        """
        self.timeout = timeout
        self._pending_messages: Dict[bytes, Dict] = {}  # Use bytes for message_id
        self._lock = threading.Lock()
        self._last_cleanup = time.time()
        self._cleanup_interval = 10.0  # Cleanup every 10 seconds
    
    def add_raw_chunk(self, message_id: bytes, chunk_index: int, total_chunks: int, 
                     chunk_data: bytes) -> Optional[Union[ProtocolMessage, str]]:
        """
        Add a raw chunk and return complete message if all chunks are received.
        
        Args:
            message_id: Unique message identifier (bytes)
            chunk_index: Index of this chunk
            total_chunks: Total number of chunks expected
            chunk_data: Raw data for this chunk
            
        Returns:
            Complete message if all chunks received, None otherwise
        """
        with self._lock:
            self._cleanup_expired_messages()
            
            # Initialize message entry if not exists
            if message_id not in self._pending_messages:
                self._pending_messages[message_id] = {
                    'chunks': {},
                    'total_chunks': total_chunks,
                    'timestamp': time.time()
                }
            
            # Store chunk
            entry = self._pending_messages[message_id]
            entry['chunks'][chunk_index] = chunk_data
            
            # Check if all chunks received
            if len(entry['chunks']) == entry['total_chunks']:
                # Reassemble message
                complete_message = self._reassemble_raw_message(message_id)
                del self._pending_messages[message_id]
                return complete_message
            
            return None
    
    def _reassemble_raw_message(self, message_id: bytes) -> Optional[Union[ProtocolMessage, str]]:
        """
        Reassemble a complete message from its raw chunks.
        
        Args:
            message_id: ID of message to reassemble
            
        Returns:
            Complete reassembled message
        """
        entry = self._pending_messages[message_id]
        chunks = entry['chunks']
        
        # Sort chunks by index and combine data
        combined_data = b''.join(chunks[i] for i in sorted(chunks.keys()))
        
        try:
            # Decode as string first
            combined_str = combined_data.decode('utf-8')
            
            # Check if it's a structured message
            from .adapter import ProtocolAdapter
            adapter = ProtocolAdapter()
            
            if adapter.is_structured_message(combined_str):
                # Structured message - decode it
                return adapter.decode_structured_message(combined_str)
            else:
                # Legacy string message
                return combined_str
                
        except UnicodeDecodeError:
            # If can't decode as UTF-8, return as bytes
            return combined_data.decode('utf-8', errors='replace')
        except Exception:
            # If anything fails, return as string
            return combined_data.decode('utf-8', errors='replace')
    
    def add_chunk(self, chunk: ProtocolMessage) -> Optional[ProtocolMessage]:
        """
        Legacy method for compatibility - converts ProtocolMessage chunks.
        
        Args:
            chunk: Chunk message to add
            
        Returns:
            Complete message if all chunks received, None otherwise
        """
        if not chunk.is_chunk or not chunk.message_id:
            return chunk  # Not a chunk, return as-is
        
        # Convert to raw chunk format
        message_id = chunk.message_id.encode('utf-8')
        chunk_data = chunk.payload.get('data', '').encode('utf-8')
        
        return self.add_raw_chunk(message_id, chunk.chunk_index, chunk.total_chunks, chunk_data)
    
    def _cleanup_expired_messages(self):
        """Clean up expired incomplete messages."""
        current_time = time.time()
        
        # Only cleanup periodically
        if current_time - self._last_cleanup < self._cleanup_interval:
            return
        
        self._last_cleanup = current_time
        expired_ids = []
        
        for message_id, entry in self._pending_messages.items():
            if current_time - entry['timestamp'] > self.timeout:
                expired_ids.append(message_id)
        
        for message_id in expired_ids:
            del self._pending_messages[message_id]
    
    def get_pending_count(self) -> int:
        """Get number of pending incomplete messages."""
        with self._lock:
            return len(self._pending_messages)


class ProtocolChunker:
    """
    Handles protocol-level chunking of messages into fixed-size chunks.
    """
    
    def __init__(self, chunk_size: int = 4096):
        """
        Initialize protocol chunker.
        
        Args:
            chunk_size: Fixed size for each chunk in bytes
        """
        self.chunk_size = chunk_size
        self.message_builder = MessageBuilder()
    
    def _generate_message_id(self) -> str:
        """Generate unique message ID for chunk tracking."""
        return str(uuid.uuid4())
    
    def should_chunk_message(self, message: ProtocolMessage) -> bool:
        """
        Determine if a message needs to be chunked.
        
        Args:
            message: Message to check
            
        Returns:
            True if message should be chunked
        """
        if message.is_chunk:
            return False  # Already a chunk
        
        message_json = message.to_json()
        return len(message_json.encode('utf-8')) > self.chunk_size
    
    def chunk_message(self, message: ProtocolMessage) -> List[ProtocolMessage]:
        """
        Split message into protocol-level chunks.
        
        Args:
            message: Message to chunk
            
        Returns:
            List of chunk messages
        """
        return self.message_builder.create_chunked_message(message, self.chunk_size)
    
    def chunk_data(self, data: str, original_type: str = "data") -> List[ProtocolMessage]:
        """
        Chunk raw data into protocol messages.
        
        Args:
            data: Raw data to chunk
            original_type: Type of the original message
            
        Returns:
            List of chunk messages
        """
        data_bytes = data.encode('utf-8')
        
        if len(data_bytes) <= self.chunk_size:
            # Single chunk
            message_id = self._generate_message_id()
            return [self.message_builder.create_chunk_from_data(
                data, 0, 1, message_id, original_type
            )]
        
        # Multiple chunks
        message_id = self._generate_message_id()
        total_chunks = (len(data_bytes) + self.chunk_size - 1) // self.chunk_size
        chunks = []
        
        for i in range(total_chunks):
            start_pos = i * self.chunk_size
            end_pos = min(start_pos + self.chunk_size, len(data_bytes))
            chunk_data = data_bytes[start_pos:end_pos].decode('utf-8', errors='replace')
            
            chunk = self.message_builder.create_chunk_from_data(
                chunk_data, i, total_chunks, message_id, original_type
            )
            chunks.append(chunk)
        
        return chunks