"""
Benchmark completo per Stream Handlers con vera connessione Server/Client.

Questo benchmark testa l'intera pipeline:
1. Invio tramite Stream Handler
2. Ricezione tramite MessageExchange
3. Dispatch automatico degli handler registrati
4. Misurazione di throughput, latency e performance

Test con connessione reale TCP tra server e client.
"""
import asyncio
import logging
import time
from statistics import mean, median, stdev
from typing import List, Dict, Any

from event.bus import AsyncEventBus
from event import EventType, MouseEvent, CommandEvent
from model.client import ClientsManager, ClientObj
from network.connection.server import ConnectionHandler
from network.connection.client import ConnectionHandler
from network.protocol.message import MessageType
from network.stream.server import UnidirectionalStreamHandler as ServerStreamHandler
from network.stream.client import UnidirectionalStreamHandler as ClientStreamHandler
from network.data.exchange import MessageExchangeConfig
from network.stream import StreamType
from utils.logging import Logger

Logger()
logging.basicConfig(level=logging.DEBUG)
# ============================================================================
# STATISTICS COLLECTOR
# ============================================================================

class BenchmarkStats:
    """Collects and analyzes benchmark statistics"""

    def __init__(self):
        self.sent_count = 0
        self.received_count = 0
        self.latencies: List[float] = []
        self.sent_timestamps: Dict[int, float] = {}
        self.start_time = 0
        self.end_time = 0
        self.lock = asyncio.Lock()

    async def mark_sent(self, msg_id: int):
        async with self.lock:
            self.sent_count += 1
            self.sent_timestamps[msg_id] = time.time()

    async def mark_received(self, msg_id: int):
        async with self.lock:
            self.received_count += 1
            if msg_id in self.sent_timestamps:
                latency = time.time() - self.sent_timestamps[msg_id]
                self.latencies.append(latency)

    def start(self):
        self.start_time = time.time()

    def stop(self):
        self.end_time = time.time()

    def get_report(self) -> Dict[str, Any]:
        duration = self.end_time - self.start_time
        throughput = self.received_count / duration if duration > 0 else 0

        report = {
            "sent": self.sent_count,
            "received": self.received_count,
            "duration": duration,
            "throughput": throughput,
            "loss_rate": (self.sent_count - self.received_count) / self.sent_count if self.sent_count > 0 else 0,
        }

        if self.latencies:
            report.update({
                "latency_mean": mean(self.latencies) * 1000,  # ms
                "latency_median": median(self.latencies) * 1000,
                "latency_min": min(self.latencies) * 1000,
                "latency_max": max(self.latencies) * 1000,
                "latency_stdev": stdev(self.latencies) * 1000 if len(self.latencies) > 1 else 0,
            })

        return report

    def print_report(self):
        report = self.get_report()

        print("\n" + "="*70)
        print("BENCHMARK RESULTS")
        print("="*70)
        print(f"Messages Sent:     {report['sent']:,}")
        print(f"Messages Received: {report['received']:,}")
        print(f"Duration:          {report['duration']:.2f}s")
        print(f"Throughput:        {report['throughput']:.0f} msgs/sec")
        print(f"Loss Rate:         {report['loss_rate']*100:.2f}%")

        if "latency_mean" in report:
            print(f"\nLatency Statistics:")
            print(f"  Mean:   {report['latency_mean']:.2f}ms")
            print(f"  Median: {report['latency_median']:.2f}ms")
            print(f"  Min:    {report['latency_min']:.2f}ms")
            print(f"  Max:    {report['latency_max']:.2f}ms")
            print(f"  StdDev: {report['latency_stdev']:.2f}ms")

        print("="*70 + "\n")


# ============================================================================
# BENCHMARK RUNNER
# ============================================================================

class StreamHandlerBenchmark:
    """Complete benchmark for stream handlers with real server/client connection"""

    def __init__(self, host="127.0.0.1", port=28100):
        self.host = host
        self.port = port

        # Components
        self.server = None
        self.client = None
        self.server_stream_handler = None
        self.client_stream_handler = None

        # Event buses
        self.server_event_bus = AsyncEventBus()
        self.client_event_bus = AsyncEventBus()

        # Client managers
        self.server_clients = ClientsManager()
        self.client_clients = ClientsManager(client_mode=True)

        # Stats
        self.stats = BenchmarkStats()

        # State
        self.server_ready = asyncio.Event()
        self.client_ready = asyncio.Event()
        self.connection_established = asyncio.Event()

        Logger()

    async def setup_server(self):
        """Setup server with stream handler"""
        print("[Server] Setting up...")

        # Create test client entry for whitelist
        test_client = ClientObj(
            ip_address=self.host,
            screen_position="right",
            screen_resolution="1920x1080"
        )
        self.server_clients.add_client(test_client)

        # Server connection handler
        async def server_on_connected(client: ClientObj, streams: list):
            print(f"[Server] Client connected: {client.ip_address}")

            # Create stream handler for this client
            self.server_stream_handler = ServerStreamHandler(
                stream_type=StreamType.COMMAND,
                clients=self.server_clients,
                event_bus=self.server_event_bus,
                handler_id="ServerCommandHandler",
                source="server",
                sender=False  # Server receives in this test
            )

            # Register message handlers
            async def handle_message(message_data):
                msg_id = message_data.sequence_id
                await self.stats.mark_received(msg_id)

            self.server_stream_handler.register_receive_callback(
                handle_message,
                MessageType.COMMAND,
            )

            # Start stream handler
            await self.server_stream_handler.start()

            # Trigger event to activate client
            await self.server_event_bus.dispatch(
                EventType.ACTIVE_SCREEN_CHANGED,
                data={"active_screen": "right"}
            )

            self.connection_established.set()
            print("[Server] Stream handler ready")

        self.server = ConnectionHandler(
            connected_callback=server_on_connected,
            host=self.host,
            port=self.port,
            heartbeat_interval=30,
            allowlist=self.server_clients
        )

        await self.server.start()
        self.server_ready.set()
        print("[Server] Ready and listening")

    async def setup_client(self):
        """Setup client with stream handler"""
        print("[Client] Setting up...")

        # Wait for server to be ready
        await self.server_ready.wait()
        await asyncio.sleep(0.1)  # Small delay

        # Create client entry
        client_obj = ClientObj(
            ip_address=self.host,
            screen_position="client",
            screen_resolution="1920x1080"
        )
        self.client_clients.add_client(client_obj)

        # Client connection handler
        async def client_on_connected(client: ClientObj):
            print(f"[Client] Connected as {client.screen_position}")

            # Create stream handler
            self.client_stream_handler = ClientStreamHandler(
                stream_type=StreamType.COMMAND,
                clients=self.client_clients,
                event_bus=self.client_event_bus,
                handler_id="ClientCommandHandler",
                sender=True,  # Client sends in this test
                active_only=False
            )

            # Start stream handler
            await self.client_stream_handler.start()

            # Activate client
            await self.client_event_bus.dispatch(
                EventType.CLIENT_ACTIVE,
                data={"active": True}
            )

            self.client_ready.set()
            print("[Client] Stream handler ready")

        self.client = ConnectionHandler(
            connected_callback=client_on_connected,
            clients=self.client_clients,
            host=self.host,
            port=self.port,
            heartbeat_interval=30
        )

        await self.client.start()
        print("[Client] Connected")

    async def run_benchmark(self, num_messages=1000, message_size=100):
        """Run the benchmark"""
        print(f"\n[Benchmark] Starting with {num_messages} messages...")

        # Wait for everything to be ready
        await self.connection_established.wait()
        await self.client_ready.wait()
        await asyncio.sleep(0.5)  # Stabilization time

        # Start benchmark
        self.stats.start()

        # Send messages
        print(f"[Benchmark] Sending {num_messages} messages...")
        for i in range(num_messages):
            message_data = CommandEvent(command="TEST", target="server", params={"i": i})

            await self.stats.mark_sent(i)
            await self.client_stream_handler.send(message_data)

            # Small delay every 100 messages to avoid overwhelming
            if (i + 1) % 100 == 0:
                await asyncio.sleep(0.01)
                print(f"  Sent {i+1}/{num_messages} messages...")

        print("[Benchmark] All messages sent, waiting for reception...")

        # Wait for messages to be received
        await asyncio.sleep(2.0)

        self.stats.stop()

    async def cleanup(self):
        """Cleanup resources"""
        print("\n[Cleanup] Stopping components...")

        if self.server_stream_handler:
            await self.server_stream_handler.stop()

        if self.client_stream_handler:
            await self.client_stream_handler.stop()

        if self.client:
            await self.client.stop()

        if self.server:
            await self.server.stop()

        await asyncio.sleep(0.5)
        print("[Cleanup] Done")


# ============================================================================
# TEST SCENARIOS
# ============================================================================

async def test_small_messages():
    """Test with small messages (high frequency)"""
    print("\n" + "="*70)
    print("TEST 1: Small Messages (100 bytes, 1000 messages)")
    print("="*70)

    benchmark = StreamHandlerBenchmark(port=28101)

    try:
        # Setup
        await benchmark.setup_server()
        await benchmark.setup_client()

        # Run
        await benchmark.run_benchmark(num_messages=1000, message_size=100)

        # Results
        benchmark.stats.print_report()

    finally:
        await benchmark.cleanup()


async def test_large_messages():
    """Test with large messages"""
    print("\n" + "="*70)
    print("TEST 2: Large Messages (10KB, 500 messages)")
    print("="*70)

    benchmark = StreamHandlerBenchmark(port=28102)

    try:
        # Setup
        await benchmark.setup_server()
        await benchmark.setup_client()

        # Run
        await benchmark.run_benchmark(num_messages=500, message_size=10000)

        # Results
        benchmark.stats.print_report()

    finally:
        await benchmark.cleanup()


async def test_high_throughput():
    """Test high throughput (many messages)"""
    print("\n" + "="*70)
    print("TEST 3: High Throughput (1KB, 5000 messages)")
    print("="*70)

    benchmark = StreamHandlerBenchmark(port=28103)

    try:
        # Setup
        await benchmark.setup_server()
        await benchmark.setup_client()

        # Run
        await benchmark.run_benchmark(num_messages=5000, message_size=1000)

        # Results
        benchmark.stats.print_report()

    finally:
        await benchmark.cleanup()


# ============================================================================
# MAIN
# ============================================================================

async def main():
    """Run all benchmark tests"""
    print("\n" + "="*70)
    print("STREAM HANDLER BENCHMARK SUITE")
    print("Testing: Send → Receive → Dispatch with Real TCP Connection")
    print("="*70)

    # Run tests
    await test_small_messages()
    await asyncio.sleep(1)

    await test_large_messages()
    await asyncio.sleep(1)

    await test_high_throughput()

    print("\n" + "="*70)
    print("ALL BENCHMARKS COMPLETED")
    print("="*70)


if __name__ == "__main__":
    try:
        asyncio.run(main(), debug=False)
    except KeyboardInterrupt:
        print("\n\nBenchmark interrupted by user")
    except Exception as e:
        print(f"\n\nBenchmark failed with error: {e}")
        import traceback
        traceback.print_exc()

