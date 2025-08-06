"""
Efficient data buffering for network operations.
Optimized for the new protocol with better memory management.
"""
import threading
import time
from collections import deque
from typing import List, Union, Optional, Tuple
from queue import Queue, Empty

from .ChunkManager import ChunkManager
from ..protocol.message import ProtocolMessage


class SmartDataBuffer:
    """
    Intelligent data buffer that optimizes batching based on message types and timing.
    Works efficiently with both structured protocol messages and legacy data.
    """
    
    def __init__(self, max_batch_size: int = 10, batch_timeout: float = 0.02):
        self.max_batch_size = max_batch_size
        self.batch_timeout = batch_timeout
        self.chunk_manager = ChunkManager()
        
        # Separate buffers for different message types
        self.structured_messages = deque()
        self.legacy_messages = deque()
        
        # Timing management
        self.last_flush_time = time.time()
        self._lock = threading.Lock()
    
    def add_message(self, message: Union[str, ProtocolMessage]) -> None:
        """
        Add a message to the appropriate buffer.
        
        Args:
            message: Message to buffer
        """
        with self._lock:
            if isinstance(message, ProtocolMessage):
                self.structured_messages.append((time.time(), message))
            else:
                self.legacy_messages.append((time.time(), message))
    
    def should_flush(self) -> bool:
        """
        Determine if the buffer should be flushed based on size or timing.
        
        Returns:
            True if buffer should be flushed
        """
        with self._lock:
            current_time = time.time()
            
            # Check size limits
            total_messages = len(self.structured_messages) + len(self.legacy_messages)
            if total_messages >= self.max_batch_size:
                return True
            
            # Check timeout
            if current_time - self.last_flush_time >= self.batch_timeout:
                return True
            
            # Special case: if we have structured messages, flush them quickly
            # to preserve timing precision
            if self.structured_messages and current_time - self.last_flush_time >= 0.01:
                return True
            
            return False
    
    def flush_ready_messages(self) -> Tuple[List[ProtocolMessage], List[str]]:
        """
        Flush messages that are ready to be sent.
        
        Returns:
            Tuple of (structured_messages, legacy_messages)
        """
        with self._lock:
            # Get all structured messages (they should be sent individually)
            structured = [msg for _, msg in self.structured_messages]
            self.structured_messages.clear()
            
            # Get legacy messages that can be batched
            legacy = [msg for _, msg in self.legacy_messages]
            self.legacy_messages.clear()
            
            self.last_flush_time = time.time()
            
            return structured, legacy
    
    def get_optimal_batches(self, legacy_messages: List[str]) -> List[Union[str, List[str]]]:
        """
        Create optimal batches from legacy messages based on size constraints.
        
        Args:
            legacy_messages: List of legacy message strings
            
        Returns:
            List of batches (single messages or batched messages)
        """
        if not legacy_messages:
            return []
        
        batches = []
        current_batch = []
        current_size = 0
        
        for message in legacy_messages:
            message_size = len(message.encode('utf-8'))
            
            # Check if adding this message would exceed chunk size
            # No delimiter overhead with fixed chunking
            batch_size = current_size + message_size + 1  # +1 for separator
            
            if (current_batch and 
                batch_size > self.chunk_manager.get_max_message_size()):
                # Current batch is full, start a new one
                if len(current_batch) == 1:
                    batches.append(current_batch[0])  # Single message
                else:
                    batches.append(current_batch)  # Batch of messages
                
                current_batch = [message]
                current_size = message_size
            else:
                # Add to current batch
                current_batch.append(message)
                current_size = batch_size
        
        # Add the last batch
        if current_batch:
            if len(current_batch) == 1:
                batches.append(current_batch[0])  # Single message
            else:
                batches.append(current_batch)  # Batch of messages
        
        return batches
    
    def is_empty(self) -> bool:
        """Check if buffer is empty."""
        with self._lock:
            return not (self.structured_messages or self.legacy_messages)
    
    def clear(self) -> None:
        """Clear all buffered messages."""
        with self._lock:
            self.structured_messages.clear()
            self.legacy_messages.clear()
            self.last_flush_time = time.time()


class BufferedMessageQueue:
    """
    Message queue with intelligent buffering for optimal network performance.
    """
    
    def __init__(self, sender_callback, max_buffer_size: int = 100):
        self.sender_callback = sender_callback
        self.max_buffer_size = max_buffer_size
        
        self.buffer = SmartDataBuffer()
        self.queue = Queue()
        
        self._running = False
        self._worker_thread = None
        self._lock = threading.Lock()
    
    def start(self) -> None:
        """Start the buffered queue processing."""
        with self._lock:
            if not self._running:
                self._running = True
                self._worker_thread = threading.Thread(target=self._process_queue, daemon=True)
                self._worker_thread.start()
    
    def stop(self) -> None:
        """Stop the buffered queue processing."""
        with self._lock:
            if self._running:
                self._running = False
                if self._worker_thread and self._worker_thread.is_alive():
                    self._worker_thread.join(timeout=1.0)
    
    def put(self, priority: int, message: Union[Tuple, str, ProtocolMessage]) -> None:
        """
        Add a message to the queue with priority.
        
        Args:
            priority: Message priority
            message: Message to send
        """
        self.queue.put((priority, message))
    
    def _process_queue(self) -> None:
        """Process messages from the queue with intelligent buffering."""
        while self._running:
            try:
                # Get message from queue
                priority, message = self.queue.get(timeout=0.01)
                
                # Extract the actual message data
                if isinstance(message, tuple) and len(message) == 2:
                    screen, data = message
                    actual_message = data
                else:
                    screen = None
                    actual_message = message
                
                # Add to buffer
                self.buffer.add_message(actual_message)
                
                # Check if we should flush
                if self.buffer.should_flush():
                    self._flush_buffer(screen, priority)
                
            except Empty:
                # Check if we should flush due to timeout
                if not self.buffer.is_empty() and self.buffer.should_flush():
                    self._flush_buffer(None, 0)
                continue
            except Exception as e:
                # Log error and continue
                continue
    
    def _flush_buffer(self, screen: Optional[str], priority: int) -> None:
        """
        Flush the buffer and send messages.
        
        Args:
            screen: Target screen
            priority: Message priority
        """
        structured_messages, legacy_messages = self.buffer.flush_ready_messages()
        
        # Send structured messages individually (preserve timestamps)
        for msg in structured_messages:
            self.sender_callback(priority, (screen, msg))
        
        # Send legacy messages optimally batched
        if legacy_messages:
            batches = self.buffer.get_optimal_batches(legacy_messages)
            for batch in batches:
                if isinstance(batch, list):
                    # Create batched message using new separator
                    batched_message = "|".join(batch)
                    self.sender_callback(priority, (screen, batched_message))
                else:
                    # Single message
                    self.sender_callback(priority, (screen, batch))