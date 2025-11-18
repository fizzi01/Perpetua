"""
Message ordering and processing for time-sensitive data.
"""
import heapq
import multiprocessing
from threading import Thread
import time
from multiprocessing import Queue, Event, Process
from typing import List, Callable
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
        self.max_delay_tolerance = max_delay_tolerance
        self.max_queue_size = max_queue_size
        self._last_processed_timestamp = 0.0

    def put(self, message: ProtocolMessage):
        """Add message to ordered queue."""
        # Use timestamp for primary ordering, sequence_id for tie-breaking
        # Negative values for min-heap behavior (earliest first)
        heapq.heappush(self._buffer, (message.timestamp, message.sequence_id, message))

        # Limit queue size to prevent memory issues
        if len(self._buffer) > self.max_queue_size:
            # Remove oldest messages if queue is full
            self._buffer = self._buffer[:self.max_queue_size]
            heapq.heapify(self._buffer)


    def get_ready_messages(self) -> List[ProtocolMessage]:
        """
        Get all messages that are ready for processing.
        Messages are ready if they are old enough to process safely.
        """
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
        return len(self._buffer)

    def is_empty(self) -> bool:
        """Check if queue is empty."""
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
        self.max_delay_tolerance = max_delay_tolerance

        if parallel_processors is None:
            parallel_processors = multiprocessing.cpu_count()
        self.parallel_processors = parallel_processors

        self._callback_thread = Thread(target=self._process_callbacks)

        # Queues for inter-process communication
        self._input_queues = [Queue(maxsize=1000) for _ in range(parallel_processors)]
        self._output_queue = Queue(maxsize=5000)

        # Worker processes and control
        self._workers: List[Process] = []
        self._stop_events = [Event() for _ in range(parallel_processors)]
        self._callback_process = None
        self._main_stop_event = Event()
        self._started = False

    def start(self):
        """Start the worker processes and callback thread."""
        if not self._started:
            try:
                self._main_stop_event.clear()

                # Start worker processes
                self._workers = []
                for i in range(self.parallel_processors):
                    self._stop_events[i].clear()
                    worker = Process(
                        target=self._worker_process,
                        args=(i, self._input_queues[i], self._output_queue,
                              self._stop_events[i], self.max_delay_tolerance),
                        daemon=True
                    )
                    worker.start()
                    self._workers.append(worker)

                # Start callback processing thread (must be in main process for callback execution)
                self._callback_thread.start()

                self._started = True
            except Exception as e:
                self._started = False
                raise

    def stop(self):
        """Stop all worker processes."""
        if self._started:
            self._main_stop_event.set()

            # Signal all workers to stop
            for event in self._stop_events:
                event.set()

            # Join workers with timeout
            for worker in self._workers:
                worker.join(timeout=1.0)
                if worker.is_alive():
                    worker.terminate()

            # Join callback thread
            if self._callback_thread and self._callback_thread.is_alive():
                self._callback_thread.join(timeout=1.0)

            self._started = False

    def is_alive(self) -> bool:
        """Check if processor is running."""
        return self._started and any(w.is_alive() for w in self._workers)

    def add_message(self, message: ProtocolMessage):
        """Add message for ordered processing."""
        if not self._started:
            return

        # Distribute messages across workers based on sequence_id
        index = message.sequence_id % self.parallel_processors
        try:
            self._input_queues[index].put_nowait(message)
        except:
            # Queue full, skip or handle based on requirements
            pass

    def _process_callbacks(self):
        """Process ready messages from output queue and execute callbacks."""
        while not self._main_stop_event.is_set():
            try:
                # Get messages from output queue
                try:
                    msg_type, messages = self._output_queue.get(timeout=0.05)
                    for message in messages:
                        self.process_callback(message)
                except:
                    pass  # Timeout, continue checking stop event

            except Exception as e:
                print(f"Error in callback processing: {e}")
                time.sleep(0.1)

    @staticmethod
    def _worker_process(worker_id: int, input_queue: Queue, output_queue: Queue,
                        stop_event: Event, max_delay_tolerance: float):
        """
        Worker process that handles message ordering.
        Runs in separate process to avoid GIL limitations.
        """
        queue = OrderedMessageQueue(max_delay_tolerance=max_delay_tolerance)
        last_flush_time = time.time()
        flush_interval = 0.5

        while not stop_event.is_set():
            try:
                # Non-blocking get to check stop_event periodically
                try:
                    message = input_queue.get(timeout=0.01)
                    queue.put(message)
                except:
                    pass

                # Get ready messages
                ready_messages = queue.get_ready_messages()
                if ready_messages:
                    output_queue.put(('ready', ready_messages))

                # Periodic flush
                current_time = time.time()
                if current_time - last_flush_time > flush_interval:
                    old_messages = queue.force_flush_old_messages()
                    if old_messages:
                        output_queue.put(('flushed', old_messages))
                    last_flush_time = current_time

                # Dynamic sleep based on activity
                if ready_messages:
                    time.sleep(0.001)
                else:
                    time.sleep(0.01)

            except Exception as e:
                print(f"OrderedMessageWorker {worker_id} error: {e}")
                time.sleep(0.1)