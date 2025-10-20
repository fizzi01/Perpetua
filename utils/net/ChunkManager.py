"""
Protocol-level chunk management system for efficient data transmission.
Handles ProtocolMessage-level chunking with internal metadata.
"""
import json
import ssl
import struct
from typing import Union, List, Tuple
from utils.net.netConstants import CHUNK_SIZE
from utils.protocol.message import ProtocolMessage, MessageBuilder
from utils.protocol.adapter import ProtocolAdapter


class ChunkManager:
    """
    Protocol-level chunk manager for network transmission.
    Chunks at ProtocolMessage level with internal chunking metadata.
    """
    
    def __init__(self, chunk_size: int = CHUNK_SIZE):
        """
        Initialize chunk manager.
        
        Args:
            chunk_size: Maximum size for each ProtocolMessage chunk when serialized
        """
        self.chunk_size = chunk_size
        self.message_builder = MessageBuilder()
        self.protocol_adapter = ProtocolAdapter(chunk_size)
        
        # For ProtocolMessage chunking, we reserve some overhead for the message structure
        # This ensures each serialized ProtocolMessage chunk fits within chunk_size
        self.max_message_size = chunk_size
    
    def send_data(self, conn, data: Union[str, ProtocolMessage]) -> None:
        """
        Send data through connection using ProtocolMessage-level chunking.
        
        Args:
            conn: Network connection
            data: Data to send (ProtocolMessage or string)
        """
        try:
            if isinstance(data, ProtocolMessage):
                # Check if message needs chunking
                if data.get_serialized_size() <= self.chunk_size:
                    # Send as single chunk
                    self._send_protocol_message(conn, data)
                else:
                    # Split into ProtocolMessage chunks
                    chunks = self.message_builder.create_chunked_message(data, self.chunk_size)
                    for chunk in chunks:
                        self._send_protocol_message(conn, chunk)
            else:
                # Legacy string data - convert to ProtocolMessage first
                legacy_message = ProtocolMessage(
                    message_type="legacy",
                    timestamp=self.message_builder._next_sequence_id(),  # Use as timestamp
                    sequence_id=self.message_builder._next_sequence_id(),
                    payload={"data": str(data)}
                )
                self.send_data(conn, legacy_message)
                
        except ssl.SSLEOFError:
            raise
        except Exception as e:
            raise ConnectionError(f"Failed to send data: {e}")
    
    def _send_protocol_message(self, conn, message: ProtocolMessage) -> None:
        """
        Send a single ProtocolMessage by serializing it to bytes.
        
        Args:
            conn: Network connection
            message: ProtocolMessage to send
        """
        print("Sending ProtocolMessage:", message)
        # Serialize ProtocolMessage directly to bytes
        message_bytes = message.to_bytes()
        
        # Send the serialized bytes
        conn.send(message_bytes)
    
    def receive_data(self, data: bytes) -> Tuple[List[Union[ProtocolMessage, str]], int]:
        """
        Process received data and return complete messages along with bytes consumed.
        Handles both ProtocolMessage chunks and legacy data.
        
        Args:
            data: Received raw bytes
            
        Returns:
            Tuple of (complete_messages, bytes_consumed)
        """
        messages = []
        offset = 0
        
        while offset < len(data):
            try:
                # Try to parse as ProtocolMessage
                remaining_data = data[offset:]
                if len(remaining_data) < 4:
                    break  # Not enough data for length prefix
                
                # Read length prefix to check if we have complete message
                try:
                    import struct
                    length = struct.unpack('>I', remaining_data[:4])[0]
                    
                    # Check if we have the complete message
                    if len(remaining_data) < 4 + length:
                        break  # Incomplete message, wait for more data
                        
                except (struct.error, ValueError):
                    # Invalid length prefix, could be legacy data
                    break
                
                # Parse ProtocolMessage from bytes
                message = ProtocolMessage.from_bytes(remaining_data)
                message_size = message.get_serialized_size()
                offset += message_size
                
                # Handle chunked messages
                if message.is_chunk:
                    # Add to reassembler
                    complete_message = self.protocol_adapter.reassembler.add_chunk(message)
                    if complete_message:
                        messages.append(complete_message)
                else:
                    # Complete message
                    messages.append(message)
                    
            except (ValueError, json.JSONDecodeError, struct.error):
                # Could be legacy data or malformed - try to find message boundary
                # For now, break and let more data accumulate
                break
        
        return messages, offset
    
    def can_batch_together(self, data1: Union[str, ProtocolMessage], 
                          data2: Union[str, ProtocolMessage]) -> bool:
        """
        Determine if two pieces of data can be batched together.
        
        With ProtocolMessage-level chunking, each message is typically sent individually
        to preserve ordering and timestamps.
        
        Args:
            data1: First data item
            data2: Second data item
            
        Returns:
            True if they can be batched together
        """
        # Don't batch ProtocolMessages - they need individual timestamps
        if isinstance(data1, ProtocolMessage) or isinstance(data2, ProtocolMessage):
            return False
        
        # For legacy strings, check if combined size fits in one message
        combined_message = ProtocolMessage(
            message_type="legacy_batch",
            timestamp=0,
            sequence_id=0,
            payload={"data": [str(data1), str(data2)]}
        )
        return combined_message.get_serialized_size() <= self.chunk_size
    
    def create_batch(self, data_items: List[Union[str, ProtocolMessage]]) -> Union[ProtocolMessage, str]:
        """
        Create a batched message from multiple data items.
        Only batches legacy string messages.
        
        Args:
            data_items: List of data items to batch
            
        Returns:
            Batched ProtocolMessage or string
        """
        # Filter out structured messages - they should be sent individually
        batchable_items = [item for item in data_items if isinstance(item, str)]
        
        if not batchable_items:
            return ""
        
        if len(batchable_items) == 1:
            return batchable_items[0]
        
        # Create batched ProtocolMessage
        return ProtocolMessage(
            message_type="legacy_batch",
            timestamp=self.message_builder._next_sequence_id(),
            sequence_id=self.message_builder._next_sequence_id(),
            payload={"batch_data": batchable_items}
        )
    
    @classmethod
    def get_max_message_size(cls) -> int:
        """Get the maximum size for a ProtocolMessage."""
        return CHUNK_SIZE
    
    def get_pending_messages_count(self) -> int:
        """Get number of pending incomplete messages in reassembler."""
        return self.protocol_adapter.reassembler.get_pending_count()