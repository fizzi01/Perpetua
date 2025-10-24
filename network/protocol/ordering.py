"""
Message ordering and processing for time-sensitive data.
"""
import heapq
import threading
import time
from typing import List, Optional, Callable
from .message import ProtocolMessage


class OrderedMessageQueue:
    """
    Queue that maintains chronological order of messages based on timestamps.
    Handles out-of-order network packets and ensures smooth event processing.
    """

    def __init__(self, max_delay_tolerance: float = 0.1, max_queue_size: int = 1000):
        """
        Initialize ordered message queue.

        Args:
            max_delay_tolerance: Maximum time to wait for delayed messages (seconds)
            max_queue_size: Maximum number of messages to queue
        """
        self._buffer = []  # Heap for ordering
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self.max_delay_tolerance = max_delay_tolerance
        self.max_queue_size = max_queue_size
        self._last_processed_timestamp = 0.0

    def put(self, message: ProtocolMessage):
        """Add message to ordered queue."""
        with self._condition:
            # Use timestamp for primary ordering, sequence_id for tie-breaking
            # Negative values for min-heap behavior (earliest first)
            heapq.heappush(self._buffer, (message.timestamp, message.sequence_id, message))

            # Limit queue size to prevent memory issues
            if len(self._buffer) > self.max_queue_size:
                # Remove oldest messages if queue is full
                self._buffer = self._buffer[:self.max_queue_size]
                heapq.heapify(self._buffer)

            self._condition.notify()

    def get_ready_messages(self) -> List[ProtocolMessage]:
        """
        Get all messages that are ready for processing.
        Messages are ready if they are old enough to process safely.
        """
        with self._condition:
            current_time = time.time()
            ready_messages = []

            # Process messages that are older than delay tolerance
            threshold_time = current_time - self.max_delay_tolerance

            while (self._buffer and
                   self._buffer[0][0] <= threshold_time):
                timestamp, seq_id, message = heapq.heappop(self._buffer)
                ready_messages.append(message)
                self._last_processed_timestamp = message.timestamp

            return ready_messages

    def force_flush_old_messages(self, max_age: float = 1.0) -> List[ProtocolMessage]:
        """
        Force flush messages older than max_age to prevent indefinite waiting.
        """
        with self._condition:
            current_time = time.time()
            old_messages = []
            cutoff_time = current_time - max_age

            # Remove and return messages older than cutoff
            remaining_buffer = []
            while self._buffer:
                timestamp, seq_id, message = heapq.heappop(self._buffer)
                if timestamp <= cutoff_time:
                    old_messages.append(message)
                else:
                    remaining_buffer.append((timestamp, seq_id, message))

            self._buffer = remaining_buffer
            heapq.heapify(self._buffer)

            return old_messages

    def size(self) -> int:
        """Get current queue size."""
        with self._lock:
            return len(self._buffer)

    def is_empty(self) -> bool:
        """Check if queue is empty."""
        with self._lock:
            return len(self._buffer) == 0


class OrderedMessageProcessor:
    """
    Processor that handles ordered message processing with timing constraints.
    """

    def __init__(self, process_callback: Callable[[ProtocolMessage], None],
                 max_delay_tolerance: float = 0.1, parallel_processors: int = 1):
        """
        Initialize processor.

        Args:
            process_callback: Function to call for each processed message
            max_delay_tolerance: Maximum delay tolerance for message ordering
        """
        self.process_callback = process_callback
        self.ordered_queues = [OrderedMessageQueue(max_delay_tolerance=max_delay_tolerance)
                                for _ in range(parallel_processors)]
        self.parallel_processors = parallel_processors
        self._processing_threads: List[threading.Thread] = []
        self._processing_thread = None
        self._stop_event = threading.Event()
        self._started = False

    def start(self):
        """Start the message processing thread."""
        if not self._started:
            try:
                self._stop_event.clear()
                self._processing_threads = []
                for i in range(self.parallel_processors):
                    thread = threading.Thread(target=self._process_messages, args=(i,), daemon=True)
                    thread.start()
                    self._processing_threads.append(thread)
                self._started = True
            except Exception as e:
                self._started = False

    def stop(self):
        """Stop the message processing."""
        if self._started:
            self._stop_event.set()
            if self._processing_thread and self._processing_thread.is_alive():
                self._processing_thread.join(timeout=1.0)
            self._started = False

    def add_message(self, message: ProtocolMessage):
        """Add message for ordered processing."""
        # Distribute messages across parallel processors based on sequence_id
        index = message.sequence_id % self.parallel_processors
        self.ordered_queues[index].put(message)

    def _process_messages(self, rank: int):
        """Main processing loop."""
        last_flush_time = time.time()
        flush_interval = 0.5  # Flush old messages every 0.5 seconds

        while not self._stop_event.is_set():
            try:
                # Get ready messages and process them
                ready_messages = self.ordered_queues[rank].get_ready_messages()
                for message in ready_messages:
                    self.process_callback(message)

                # Periodically flush very old messages to prevent blocking
                current_time = time.time()
                if current_time - last_flush_time > flush_interval:
                    old_messages = self.ordered_queues[rank].force_flush_old_messages()
                    for message in old_messages:
                        self.process_callback(message)
                    last_flush_time = current_time

                # Adjust sleep based on queue status
                if ready_messages:
                    time.sleep(0.005)  # Short sleep if processing messages
                else:
                    time.sleep(0.02)  # Longer sleep if no messages ready

            except Exception as e:
                # Log error but continue processing
                print(f"Error in message processing: {e}")
                time.sleep(0.1)

    def is_alive(self) -> bool:
        """Check if processor is running."""
        return self._started and (
                self._processing_thread is None or
                self._processing_thread.is_alive()
        )