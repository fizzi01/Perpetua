"""
Improved chunk management system for efficient data transmission.
Handles both structured protocol messages and legacy data with better performance.
"""
import ssl
from typing import Union, List
from utils.net.netConstants import CHUNK_SIZE, END_DELIMITER, CHUNK_DELIMITER
from utils.protocol.message import ProtocolMessage
from utils.protocol.adapter import ProtocolAdapter


class ChunkManager:
    """
    Efficient chunk management for network transmission.
    Handles both structured protocol messages and legacy string data.
    """
    
    # Reserve space for delimiters and safety margin
    DELIMITER_OVERHEAD = max(len(END_DELIMITER.encode()), len(CHUNK_DELIMITER.encode()))
    SAFETY_MARGIN = 64  # Extra safety margin for encoding differences
    EFFECTIVE_CHUNK_SIZE = CHUNK_SIZE - DELIMITER_OVERHEAD - SAFETY_MARGIN
    
    def __init__(self):
        self.protocol_adapter = ProtocolAdapter()
    
    def prepare_for_transmission(self, data: Union[str, ProtocolMessage]) -> str:
        """
        Prepare data for transmission, handling both structured and legacy formats.
        
        Args:
            data: Either a structured ProtocolMessage or legacy string
            
        Returns:
            String ready for transmission
        """
        if isinstance(data, ProtocolMessage):
            # Use structured format for protocol messages
            return self.protocol_adapter.encode_structured_message(data)
        elif isinstance(data, str):
            # Legacy string data - pass through
            return data
        else:
            # Convert other types to string
            return str(data)
    
    def send_data(self, conn, data: Union[str, ProtocolMessage]) -> None:
        """
        Send data through connection with efficient chunking.
        
        Args:
            conn: Network connection
            data: Data to send (ProtocolMessage or string)
        """
        try:
            # Prepare data for transmission
            transmission_data = self.prepare_for_transmission(data)
            
            # Convert to bytes for size calculation
            data_bytes = transmission_data.encode('utf-8')
            data_length = len(data_bytes)
            
            if data_length <= self.EFFECTIVE_CHUNK_SIZE:
                # Single chunk - add end delimiter
                self._send_single_chunk(conn, data_bytes)
            else:
                # Multiple chunks needed
                self._send_multiple_chunks(conn, data_bytes)
                
        except ssl.SSLEOFError:
            raise
        except Exception as e:
            raise ConnectionError(f"Failed to send data: {e}")
    
    def _send_single_chunk(self, conn, data_bytes: bytes) -> None:
        """Send a single chunk with end delimiter."""
        conn.send(data_bytes + END_DELIMITER.encode('utf-8'))
    
    def _send_multiple_chunks(self, conn, data_bytes: bytes) -> None:
        """Send data in multiple chunks with appropriate delimiters."""
        chunks = self._split_into_chunks(data_bytes)
        
        for i, chunk in enumerate(chunks):
            if i == len(chunks) - 1:
                # Last chunk - use end delimiter
                conn.send(chunk + END_DELIMITER.encode('utf-8'))
            else:
                # Intermediate chunk - use chunk delimiter
                conn.send(chunk + CHUNK_DELIMITER.encode('utf-8'))
    
    def _split_into_chunks(self, data_bytes: bytes) -> List[bytes]:
        """Split data into appropriately sized chunks."""
        chunks = []
        offset = 0
        
        while offset < len(data_bytes):
            chunk_end = min(offset + self.EFFECTIVE_CHUNK_SIZE, len(data_bytes))
            chunks.append(data_bytes[offset:chunk_end])
            offset = chunk_end
            
        return chunks
    
    def can_batch_together(self, data1: Union[str, ProtocolMessage], 
                          data2: Union[str, ProtocolMessage]) -> bool:
        """
        Determine if two pieces of data can be batched together efficiently.
        
        Args:
            data1: First data item
            data2: Second data item
            
        Returns:
            True if they can be batched together
        """
        # Don't batch structured messages - they need individual timestamps
        if isinstance(data1, ProtocolMessage) or isinstance(data2, ProtocolMessage):
            return False
        
        # Check combined size
        combined_size = len(str(data1).encode('utf-8')) + len(str(data2).encode('utf-8'))
        combined_size += len(CHUNK_DELIMITER.encode('utf-8'))  # Delimiter between items
        
        return combined_size <= self.EFFECTIVE_CHUNK_SIZE
    
    def create_batch(self, data_items: List[Union[str, ProtocolMessage]]) -> str:
        """
        Create a batched message from multiple data items.
        Only batches legacy string messages for efficiency.
        
        Args:
            data_items: List of data items to batch
            
        Returns:
            Batched message string
        """
        # Filter out structured messages - they should be sent individually
        batchable_items = [item for item in data_items if isinstance(item, str)]
        
        if not batchable_items:
            return ""
        
        # Join with chunk delimiter
        return CHUNK_DELIMITER.join(batchable_items)
    
    @classmethod
    def get_max_message_size(cls) -> int:
        """Get the maximum size for a single message."""
        return cls.EFFECTIVE_CHUNK_SIZE