"""
Protocol-level chunk management system for efficient data transmission.
Handles fixed-size chunking without visible delimiters.
"""
import ssl
import struct
from typing import Union, List
from utils.net.netConstants import CHUNK_SIZE
from utils.protocol.message import ProtocolMessage
from utils.protocol.adapter import ProtocolAdapter


class ChunkManager:
    """
    Protocol-level chunk manager for network transmission.
    Uses fixed chunk sizes without delimiters, with protocol-level reassembly.
    """
    
    def __init__(self, chunk_size: int = CHUNK_SIZE):
        """
        Initialize chunk manager.
        
        Args:
            chunk_size: Fixed size for each chunk (default: 4096 bytes)
        """
        self.chunk_size = chunk_size
        self.protocol_adapter = ProtocolAdapter(chunk_size)
        
        # Reserve space for chunk metadata header
        # Format: [message_id(16 bytes)][chunk_index(4 bytes)][total_chunks(4 bytes)][data_size(4 bytes)]
        self.header_size = 28
        self.data_size = chunk_size - self.header_size
    
    def send_data(self, conn, data: Union[str, ProtocolMessage]) -> None:
        """
        Send data through connection using protocol-level chunking.
        
        Args:
            conn: Network connection
            data: Data to send (ProtocolMessage or string)
        """
        try:
            if isinstance(data, ProtocolMessage):
                # Structured message - encode to JSON first
                json_data = self.protocol_adapter.encode_structured_message(data)
                data_bytes = json_data.encode('utf-8')
            else:
                # Legacy string data
                data_bytes = str(data).encode('utf-8')
            
            # Send as chunks
            self._send_chunked_data(conn, data_bytes)
                
        except ssl.SSLEOFError:
            raise
        except Exception as e:
            raise ConnectionError(f"Failed to send data: {e}")
    
    def _send_chunked_data(self, conn, data_bytes: bytes) -> None:
        """
        Send data as fixed-size chunks with minimal metadata.
        
        Args:
            conn: Network connection
            data_bytes: Data to send as bytes
        """
        import uuid
        
        # Generate unique message ID
        message_id = uuid.uuid4().bytes  # 16 bytes
        
        # Calculate number of chunks needed
        total_chunks = (len(data_bytes) + self.data_size - 1) // self.data_size
        
        # Send each chunk
        for chunk_index in range(total_chunks):
            start_pos = chunk_index * self.data_size
            end_pos = min(start_pos + self.data_size, len(data_bytes))
            chunk_data = data_bytes[start_pos:end_pos]
            
            # Create header: message_id(16) + chunk_index(4) + total_chunks(4) + data_size(4)
            header = message_id + struct.pack('>III', chunk_index, total_chunks, len(chunk_data))
            
            # Create fixed-size chunk
            chunk = header + chunk_data
            
            # Pad to exact chunk size
            if len(chunk) < self.chunk_size:
                chunk += b'\x00' * (self.chunk_size - len(chunk))
            
            conn.send(chunk)
    
    def receive_chunk(self, data: bytes) -> Union[ProtocolMessage, str, None]:
        """
        Process received chunk data and return complete message if available.
        
        Args:
            data: Received chunk data (exactly chunk_size bytes)
            
        Returns:
            Complete message if ready, None if more chunks needed
        """
        if len(data) != self.chunk_size:
            return None
        
        try:
            # Parse header
            message_id = data[:16]
            chunk_index, total_chunks, data_size = struct.unpack('>III', data[16:28])
            chunk_data = data[28:28+data_size]
            
            # Add to reassembler
            return self.protocol_adapter.reassembler.add_raw_chunk(
                message_id, chunk_index, total_chunks, chunk_data
            )
            
        except struct.error:
            return None
        except Exception:
            return None
    
    def can_batch_together(self, data1: Union[str, ProtocolMessage], 
                          data2: Union[str, ProtocolMessage]) -> bool:
        """
        Determine if two pieces of data can be batched together.
        
        Note: With protocol-level chunking, batching is handled differently.
        Structured messages are generally not batched to preserve timestamps.
        
        Args:
            data1: First data item
            data2: Second data item
            
        Returns:
            True if they can be batched together
        """
        # Don't batch structured messages - they need individual timestamps
        if isinstance(data1, ProtocolMessage) or isinstance(data2, ProtocolMessage):
            return False
        
        # For legacy strings, check if combined size fits in one chunk
        combined_size = len(str(data1).encode('utf-8')) + len(str(data2).encode('utf-8'))
        return combined_size <= self.data_size
    
    def create_batch(self, data_items: List[Union[str, ProtocolMessage]]) -> str:
        """
        Create a batched message from multiple data items.
        Only batches legacy string messages.
        
        Args:
            data_items: List of data items to batch
            
        Returns:
            Batched message string
        """
        # Filter out structured messages - they should be sent individually
        batchable_items = [item for item in data_items if isinstance(item, str)]
        
        if not batchable_items:
            return ""
        
        # Join with separator (but no delimiter overhead since we use fixed chunks)
        return "|".join(batchable_items)
    
    @classmethod
    def get_max_message_size(cls) -> int:
        """Get the maximum size for a single chunk's data payload."""
        return CHUNK_SIZE - 28  # Subtract header size
    
    def get_pending_messages_count(self) -> int:
        """Get number of pending incomplete messages in reassembler."""
        return self.protocol_adapter.reassembler.get_pending_count()