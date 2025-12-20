import asyncio
from collections import deque
from dataclasses import dataclass, field
from time import time
from typing import Dict, Optional

from ..logging import get_logger


@dataclass
class ConnectionMetrics:
    """
    Represents and tracks network connection metrics, such as data throughput, latency,
    error rates, and uptime.

    This class collects metrics about a network connection, including data sent and received,
    latency statistics, error counts, TLS handshake performance, and network activity. It provides
    functionality to calculate throughput and export the collected metrics as a dictionary.

    Attributes:
        connection_id (str): The unique identifier of the connection.
        start_time (float): The timestamp of when the connection was started.
        bytes_sent (int): The total number of bytes sent over the connection.
        bytes_received (int): The total number of bytes received over the connection.
        messages_sent (int): The total number of messages sent over the connection.
        messages_received (int): The total number of messages received over the connection.
        avg_latency (float): The average latency of the connection, represented in seconds.
        min_latency (float): The minimum observed latency over the connection's lifetime, in seconds.
        max_latency (float): The maximum observed latency over the connection's lifetime, in seconds.
        connection_errors (int): The total number of connection errors recorded.
        reconnections (int): The total number of reconnection attempts for the connection.
        packet_loss (int): The number of packets lost during transmission.
        chunks_received (int): The total number of data chunks received.
        tls_handshake_time (Optional[float]): The time taken for the most recent TLS handshake, in seconds.
        last_active (float): The timestamp of the connection's last observed activity.
    """

    # Stream ID
    connection_id: str
    start_time: float = field(default_factory=time)

    # Throughput
    bytes_sent: int = 0
    bytes_received: int = 0
    messages_sent: int = 0
    messages_received: int = 0

    # Latency
    avg_latency: float = 0.0
    min_latency: float = float("inf")
    max_latency: float = 0.0
    _latency_samples: deque = field(default_factory=lambda: deque(maxlen=1000))

    # Errors and QOL
    connection_errors: int = 0
    reconnections: int = 0
    packet_loss: int = 0
    chunks_received: int = 0

    # Performance TLS
    tls_handshake_time: Optional[float] = None

    # Network
    last_active: float = field(default_factory=time)

    def record_sent(self, size: int):
        """
        Records data sent over the connection.

        Args:
            size: Number of bytes sent.
        """
        self.bytes_sent += size
        self.messages_sent += 1
        self.last_active = time()

    def record_received(self, size: int):
        """
        Records data received over the connection.

        Args:
            size: Number of bytes received.
        """
        self.bytes_received += size
        self.messages_received += 1
        self.last_active = time()

    def record_latency(self, latency: float):
        """
        Register a new latency sample and update min, max, and average latency metrics.
        Args:
            latency: Latency sample in seconds.
        """
        self._latency_samples.append(latency)
        self.min_latency = min(self.min_latency, latency)
        self.max_latency = max(self.max_latency, latency)

    def calculate_avg_latency(self) -> float:
        """
        Calculate and update the average latency from recorded samples.

        Returns:
            The calculated average latency in seconds.
        """
        if self._latency_samples:
            try:
                self.avg_latency = sum(self._latency_samples) / len(
                    self._latency_samples
                )
            except ZeroDivisionError:
                self.avg_latency = 0.0
        return self.avg_latency

    def get_throughput(self) -> Dict[str, float]:
        """
        Throughput calculated in bytes/sec and messages/sec.

        Returns:
            A dictionary with 'bytes_per_sec','messages_per_sec' and 'uptime'.
        """
        duration = time() - self.start_time
        if duration == 0:
            return {"bytes_per_sec": 0, "messages_per_sec": 0}

        return {
            "bytes_per_sec": (self.bytes_sent + self.bytes_received) / duration,
            "messages_per_sec": (self.messages_sent + self.messages_received)
                                / duration,
            "uptime": duration,
        }

    def to_dict(self) -> Dict:
        throughput = self.get_throughput()
        return {
            "connection_id": self.connection_id,
            "uptime": throughput.get("uptime", 0),
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "throughput_bytes_sec": throughput.get("bytes_per_sec", 0),
            "throughput_msg_sec": throughput.get("messages_per_sec", 0),
            "latency_avg_ms": self.avg_latency * 1000,
            "latency_min_ms": self.min_latency * 1000
            if self.min_latency != float("inf")
            else 0,
            "latency_max_ms": self.max_latency * 1000,
            "errors": self.connection_errors,
            "reconnections": self.reconnections,
            "packet_loss": self.packet_loss,
            "tls_handshake_ms": self.tls_handshake_time * 1000
            if self.tls_handshake_time
            else None,
        }


class MetricsCollector:
    """
    Manages the collection, retrieval, and logging of connection metrics.

    MetricsCollector is responsible for handling metrics for multiple connections. It
    provides a thread-safe way to register, retrieve, and remove connection metrics, and
    logs summaries of all managed metrics. The purpose of this class is to centralize the
    management of connection-specific metrics in an asynchronous context.
    """

    def __init__(self):
        self._connections: Dict[str, ConnectionMetrics] = {}
        self._lock = asyncio.Lock()
        self._logger = get_logger(f"{self.__class__.__name__}")

    async def register_connection(self, connection_id: str) -> ConnectionMetrics:
        """
        Register a new connection for metrics tracking.

        Args:
            connection_id: The unique identifier for the connection.

        Returns:
            The new connection metrics object.
        """
        async with self._lock:
            metrics = ConnectionMetrics(connection_id=connection_id)
            self._connections[connection_id] = metrics
            return metrics

    async def get_metrics(self, connection_id: str) -> Optional[ConnectionMetrics]:
        """
        Retrieve metrics for a specific connection.

        Args:
            connection_id: The unique identifier for the connection.

        Returns:
            The connection metrics object, or None if not found.
        """
        return self._connections.get(connection_id)

    async def remove_connection(self, connection_id: str):
        """
        Remove a connection from metrics tracking.

        Args:
            connection_id: The unique identifier for the connection.
        """
        async with self._lock:
            if connection_id in self._connections:
                del self._connections[connection_id]

    async def get_all_metrics(self) -> Dict[str, Dict]:
        """
        Retrieve metrics for all registered connections.

        Returns:
            A dictionary mapping connection IDs to their metrics dictionaries.
        """
        # Invoke calculate_avg_latency for all connections
        for m in self._connections.values():
            m.calculate_avg_latency()
        return {cid: m.to_dict() for cid, m in self._connections.items()}

    async def log_summary(self):
        """
        Log a summary of all connection metrics.
        """
        all_metrics = await self.get_all_metrics()
        for conn_id, metrics in all_metrics.items():
            self._logger.info(f"Connection {conn_id} metrics", **metrics)


class PerformanceMonitor:
    """
    Monitors system performance metrics at a regular interval.

    The PerformanceMonitor class orchestrates periodic collection and analysis
    of performance metrics using a MetricsCollector instance. It operates
    asynchronously, allowing concurrent tasks to proceed while monitoring occurs.
    This class is primarily designed for tracking metrics like throughput,
    latency, and errors and provides structured logging for significant events.

    Attributes:
        collector (MetricsCollector): Instance of a metrics collector responsible
            for retrieving performance metrics.
        interval (float): Time interval in seconds between metric collection cycles.
    """

    def __init__(self, collector: MetricsCollector, interval: float = 10.0):
        self.collector = collector
        self.interval = interval
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._logger = get_logger(f"{self.__class__.__name__}")

    async def start(self):
        """
        Start the monitoring loop.
        """
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self):
        """
        Stop the monitoring loop.
        """
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self):
        while self._running:
            try:
                await asyncio.sleep(self.interval)

                metrics = await self.collector.get_all_metrics()

                for conn_id, m in metrics.items():
                    # Log metriche significative
                    if m["throughput_msg_sec"] > 0:
                        self._logger.info(
                            f"Performance {conn_id}",
                            throughput_mbps=f"{m['throughput_bytes_sec'] / 1_000_000:.2f}",
                            msg_per_sec=f"{m['throughput_msg_sec']:.1f}",
                            avg_latency_ms=f"{m['latency_avg_ms']:.2f}",
                            errors=m["errors"],
                        )

                    await asyncio.sleep(0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Error in monitor loop -> {e}")
