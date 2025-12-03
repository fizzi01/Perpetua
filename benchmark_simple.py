"""
Benchmark per testare send/receive messaggi tra AsyncServer e AsyncClient.

Misura throughput e latency in scenari realistici con VERA comunicazione bidirezionale.
Il server riceve e conta i messaggi, verificando che la comunicazione funzioni.
"""
import asyncio
from time import time
from statistics import mean
from network.connection.AsyncClientConnectionService import AsyncClientConnectionHandler
from network.connection.AsyncServerConnectionService import AsyncServerConnectionHandler
from network.data.MessageExchange import MessageExchange, MessageExchangeConfig
from network.protocol.message import ProtocolMessage
from network.stream import StreamType
from model.ClientObj import ClientObj, ClientsManager
from utils.logging import Logger


class MessageCounter:
    """Classe per tracciare messaggi ricevuti in modo thread-safe"""
    def __init__(self):
        self.count = 0
        self.messages = []
        self.lock = asyncio.Lock()

    async def add(self, msg):
        async with self.lock:
            self.count += 1
            self.messages.append(msg)

    async def get_count(self):
        async with self.lock:
            return self.count

    async def reset(self):
        async with self.lock:
            self.count = 0
            self.messages.clear()




async def benchmark_server_client():
    """Benchmark completo server-client communication con VERA ricezione messaggi"""
    print("="*70)
    print("Server-Client Message Exchange Benchmark (Bidirectional)")
    print("="*70)
    print()

    # Setup logging (minimal)
    Logger(stdout=print, logging=False)

    # ========================================================================
    # SETUP SERVER CON MESSAGE HANDLER
    # ========================================================================
    print("Setting up server and client...")

    # Counter per messaggi ricevuti dal server
    server_counter = MessageCounter()

    # Server setup con MessageExchange per ricevere messaggi
    server_clients = ClientsManager()
    test_client = ClientObj(
        ip_address="127.0.0.1",
        screen_position="right",
        screen_resolution="1920x1080"
    )
    server_clients.add_client(test_client)

    # Variabili per tracciare connessione e MessageExchange
    server_msg_exchange = None
    server_receive_task = None

    async def server_on_connected(client):
        nonlocal server_msg_exchange, server_receive_task
        print(f"  Server: Client {client.ip_address} connected")

        # Ottieni il socket del client connesso
        if client.conn_socket:
            # Crea MessageExchange per il server per ricevere messaggi
            config = MessageExchangeConfig(
                max_chunk_size=8192,
                auto_chunk=True,
                receive_buffer_size=65536
            )
            server_msg_exchange = MessageExchange(config)

            # Setup transport usando gli stream del client
            command_writer = client.conn_socket.get_writer(StreamType.COMMAND)
            command_reader = client.conn_socket.get_reader(StreamType.COMMAND)

            if command_writer and command_reader:
                async def server_send(data: bytes):
                    command_writer.write(data)
                    await command_writer.drain()

                async def server_recv(size: int) -> bytes:
                    return await command_reader.read(size)

                server_msg_exchange.set_transport(server_send, server_recv)

                # Registra handler per messaggi
                async def handle_mouse(msg: ProtocolMessage):
                    await server_counter.add(msg)

                async def handle_keyboard(msg: ProtocolMessage):
                    await server_counter.add(msg)

                async def handle_clipboard(msg: ProtocolMessage):
                    await server_counter.add(msg)

                server_msg_exchange.register_handler("mouse", handle_mouse)
                server_msg_exchange.register_handler("keyboard", handle_keyboard)
                server_msg_exchange.register_handler("clipboard", handle_clipboard)

                # Avvia receive loop
                await server_msg_exchange.start()

                # Task per processare messaggi ricevuti
                async def process_server_messages():
                    while True:
                        try:
                            msg = await server_msg_exchange.get_received_message(timeout=0.01)
                            if msg:
                                await server_msg_exchange.dispatch_message(msg)
                        except asyncio.TimeoutError:
                            continue
                        except asyncio.CancelledError:
                            break
                        except Exception as e:
                            print(f"Error processing message: {e}")
                            break

                server_receive_task = asyncio.create_task(process_server_messages())

    server = AsyncServerConnectionHandler(
        connected_callback=server_on_connected,
        host="127.0.0.1",
        port=28000,
        heartbeat_interval=30,
        whitelist=server_clients
    )

    # ========================================================================
    # SETUP CLIENT
    # ========================================================================
    client_connected = [False]

    async def client_on_connected(client):
        client_connected[0] = True
        print(f"  Client: Connected as {client.screen_position}")

    client = AsyncClientConnectionHandler(
        connected_callback=client_on_connected,
        host="127.0.0.1",
        port=28000,
        wait=1,
        heartbeat_interval=30,
        auto_reconnect=False,
        open_streams=[]
    )

    # Start server
    if not await server.start():
        print("❌ Failed to start server")
        return

    await asyncio.sleep(0.5)

    # Start client
    if not await client.start():
        print("❌ Failed to start client")
        await server.stop()
        return

    # Wait for connection and server message exchange setup
    for _ in range(50):  # 5 seconds max
        if client.is_connected() and server_msg_exchange is not None:
            break
        await asyncio.sleep(0.1)

    if not client.is_connected() or server_msg_exchange is None:
        print("❌ Client failed to connect or server message exchange not ready")
        await client.stop()
        await server.stop()
        return

    print("✓ Server and client connected")
    print("✓ Message handlers registered\n")

    # ========================================================================
    # BENCHMARK 1: Simple Throughput (con verifica ricezione)
    # ========================================================================
    print("=== Benchmark 1: Simple Throughput (with receive verification) ===")

    num_messages = 15_000
    await server_counter.reset()

    start = time()
    for i in range(num_messages):
        await client.send_message(
            StreamType.MOUSE,
            x=i, y=i, event="move", dx=1, dy=1
        )
    send_time = time() - start

    # Attendi che tutti i messaggi siano ricevuti
    print(f"  Sent {num_messages} messages in {send_time:.3f}s")
    print(f"  Waiting for server to receive...")

    for _ in range(100):  # Max 10 secondi
        received = await server_counter.get_count()
        if received >= num_messages:
            break
        await asyncio.sleep(0.1)

    total_time = time() - start
    received = await server_counter.get_count()

    send_throughput = num_messages / send_time
    total_throughput = received / total_time if total_time > 0 else 0

    print(f"Messages sent:        {num_messages}")
    print(f"Messages received:    {received}")
    print(f"Send time:            {send_time:.3f}s")
    print(f"Total time:           {total_time:.3f}s")
    print(f"Send throughput:      {send_throughput:.0f} msg/s")
    print(f"Total throughput:     {total_throughput:.0f} msg/s")
    print(f"Success rate:         {received/num_messages*100:.1f}%")
    print()

    simple_throughput = total_throughput

    # ========================================================================
    # BENCHMARK 2: Burst Send (con verifica ricezione)
    # ========================================================================
    print("=== Benchmark 2: Burst Send (10 bursts x 500 msgs) ===")

    burst_size = 500
    num_bursts = 10
    burst_times = []
    await server_counter.reset()

    for burst_num in range(num_bursts):
        burst_start = time()

        for i in range(burst_size):
            await client.send_message(
                StreamType.MOUSE,
                x=i, y=i, event="move", dx=1, dy=1
            )

        burst_time = time() - burst_start
        burst_times.append(burst_time)
        await asyncio.sleep(0.05)

    # Attendi ricezione
    total_expected = burst_size * num_bursts
    for _ in range(100):
        received = await server_counter.get_count()
        if received >= total_expected:
            break
        await asyncio.sleep(0.1)

    received = await server_counter.get_count()
    avg_burst_time = mean(burst_times)
    burst_throughput = burst_size / avg_burst_time

    print(f"Bursts:               {num_bursts}")
    print(f"Msgs per burst:       {burst_size}")
    print(f"Total sent:           {total_expected}")
    print(f"Total received:       {received}")
    print(f"Avg burst time:       {avg_burst_time:.3f}s")
    print(f"Send throughput:      {burst_throughput:.0f} msg/s")
    print(f"Success rate:         {received/total_expected*100:.1f}%")
    print()

    # ========================================================================
    # BENCHMARK 3: Sustained Load (con verifica ricezione)
    # ========================================================================
    print("=== Benchmark 3: Sustained Load (3 seconds) ===")

    duration = 3.0
    await server_counter.reset()
    start = time()
    message_count = 0

    while time() - start < duration:
        await client.send_message(
            StreamType.MOUSE,
            x=message_count, y=message_count, event="move", dx=1, dy=1
        )
        message_count += 1

    send_time = time() - start

    # Attendi ricezione
    print(f"  Sent {message_count} messages in {send_time:.2f}s")
    print(f"  Waiting for server to receive...")

    for _ in range(100):
        received = await server_counter.get_count()
        if received >= message_count:
            break
        await asyncio.sleep(0.1)

    total_time = time() - start
    received = await server_counter.get_count()
    sustained_throughput = received / total_time if total_time > 0 else 0

    print(f"Messages sent:        {message_count}")
    print(f"Messages received:    {received}")
    print(f"Duration:             {total_time:.2f}s")
    print(f"Throughput:           {sustained_throughput:.0f} msg/s")
    print(f"Success rate:         {received/message_count*100:.1f}%")
    print()

    # ========================================================================
    # BENCHMARK 4: Different Message Types (con verifica ricezione)
    # ========================================================================
    print("=== Benchmark 4: Different Message Types ===")

    test_cases = [
        ("MOUSE", StreamType.MOUSE, {"x": 100, "y": 200, "event": "click", "dx": 0, "dy": 0}, 1000),
        ("KEYBOARD", StreamType.KEYBOARD, {"key": "A", "event": "press"}, 1000),
        ("CLIPBOARD", StreamType.CLIPBOARD, {"content": "Test" * 100, "content_type": "text"}, 500),
    ]

    for msg_type, stream_type, kwargs, count in test_cases:
        await server_counter.reset()
        start = time()

        for i in range(count):
            await client.send_message(stream_type, **kwargs)

        send_time = time() - start

        # Attendi ricezione
        for _ in range(50):
            received = await server_counter.get_count()
            if received >= count:
                break
            await asyncio.sleep(0.1)

        total_time = time() - start
        received = await server_counter.get_count()
        throughput = received / total_time if total_time > 0 else 0

        print(f"{msg_type:12} - sent:{count:4} recv:{received:4} time:{total_time:.2f}s rate:{throughput:.0f} msg/s")

    print()

    # ========================================================================
    # SUMMARY
    # ========================================================================
    print("="*70)
    print("BENCHMARK COMPLETE")
    print("="*70)
    print()
    print("Summary (end-to-end throughput with receive verification):")
    print(f"  Simple throughput:    {simple_throughput:.0f} msg/s")
    print(f"  Burst throughput:     {burst_throughput:.0f} msg/s")
    print(f"  Sustained throughput: {sustained_throughput:.0f} msg/s")
    print()
    print("✅ All benchmarks completed successfully!")
    print("="*70)

    # ========================================================================
    # CLEANUP
    # ========================================================================
    print("\nCleaning up...")

    if server_receive_task:
        server_receive_task.cancel()
        try:
            await server_receive_task
        except asyncio.CancelledError:
            pass

    if server_msg_exchange:
        await server_msg_exchange.stop()

    await client.stop()
    await server.stop()
    await asyncio.sleep(0.3)
    print("✓ Cleanup complete")
    """Benchmark completo server-client communication"""




async def benchmark_quick():
    """Quick benchmark with message reception verification"""
    print("="*70)
    print("Quick Server-Client Benchmark (with receive verification)")
    print("="*70)
    print()

    Logger(stdout=print, logging=False)

    # Counter per messaggi ricevuti
    counter = MessageCounter()

    # Setup server con message handler
    server_clients = ClientsManager()
    test_client = ClientObj(
        ip_address="127.0.0.1",
        screen_position="test",
        screen_resolution="1920x1080"
    )
    server_clients.add_client(test_client)

    server_msg_exchange = None
    server_receive_task = None

    async def server_on_connected(client):
        nonlocal server_msg_exchange, server_receive_task
        print(f"  Server: Client connected")

        if client.conn_socket:
            config = MessageExchangeConfig(max_chunk_size=8192, auto_chunk=True)
            server_msg_exchange = MessageExchange(config)

            writer = client.conn_socket.get_writer(StreamType.COMMAND)
            reader = client.conn_socket.get_reader(StreamType.COMMAND)

            if writer and reader:
                server_msg_exchange.set_transport(
                    lambda d: writer.write(d) or writer.drain(),
                    lambda s: reader.read(s)
                )

                async def handle_message(msg):
                    await counter.add(msg)

                server_msg_exchange.register_handler("mouse", handle_message)
                await server_msg_exchange.start()

                async def process_messages():
                    while True:
                        try:
                            msg = await server_msg_exchange.get_received_message(timeout=0.01)
                            if msg:
                                await server_msg_exchange.dispatch_message(msg)
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            break

                server_receive_task = asyncio.create_task(process_messages())

    server = AsyncServerConnectionHandler(
        connected_callback=server_on_connected,
        host="127.0.0.1",
        port=28001,
        heartbeat_interval=30,
        whitelist=server_clients
    )

    client = AsyncClientConnectionHandler(
        host="127.0.0.1",
        port=28001,
        auto_reconnect=False,
        open_streams=[]
    )

    # Start
    print("Starting server and client...")
    await server.start()
    await asyncio.sleep(0.5)
    await client.start()

    # Wait for connection
    for _ in range(50):
        if client.is_connected() and server_msg_exchange is not None:
            break
        await asyncio.sleep(0.1)

    if not client.is_connected():
        print("❌ Connection failed")
        await client.stop()
        await server.stop()
        return

    print("✓ Connected\n")

    # Quick test
    print("=== Quick Throughput Test (with receive verification) ===")
    num_messages = 2000

    start = time()
    for i in range(num_messages):
        await client.send_message(
            StreamType.MOUSE,
            x=i, y=i, event="move", dx=1, dy=1
        )
    send_time = time() - start

    # Wait for reception
    print(f"  Sent {num_messages} in {send_time:.3f}s, waiting for reception...")
    for _ in range(100):
        received = await counter.get_count()
        if received >= num_messages:
            break
        await asyncio.sleep(0.1)

    total_time = time() - start
    received = await counter.get_count()
    throughput = received / total_time if total_time > 0 else 0

    print(f"Messages sent:    {num_messages}")
    print(f"Messages recv:    {received}")
    print(f"Time:             {total_time:.3f}s")
    print(f"Throughput:       {throughput:.0f} msg/s")
    print(f"Success rate:     {received/num_messages*100:.1f}%")
    print()

    print("="*70)
    print("✅ Quick benchmark complete!")
    print("="*70)

    # Cleanup
    if server_receive_task:
        server_receive_task.cancel()
    if server_msg_exchange:
        await server_msg_exchange.stop()

    await client.stop()
    await server.stop()

    """Quick benchmark with fewer messages"""
    print("="*70)
    print("Quick Server-Client Benchmark")
    print("="*70)
    print()

    Logger(stdout=print, logging=False)

    # Setup
    server_clients = ClientsManager()
    test_client = ClientObj(
        ip_address="127.0.0.1",
        screen_position="test",
        screen_resolution="1920x1080"
    )
    server_clients.add_client(test_client)

    server = AsyncServerConnectionHandler(
        host="127.0.0.1",
        port=28001,
        heartbeat_interval=30,
        whitelist=server_clients
    )

    client = AsyncClientConnectionHandler(
        host="127.0.0.1",
        port=28001,
        auto_reconnect=False,
        open_streams=[]
    )

    # Start
    print("Starting server and client...")
    await server.start()
    await asyncio.sleep(0.3)
    await client.start()

    # Wait for connection
    for _ in range(30):
        if client.is_connected():
            break
        await asyncio.sleep(0.1)

    if not client.is_connected():
        print("❌ Connection failed")
        await client.stop()
        await server.stop()
        return

    print("✓ Connected\n")

    # Quick test
    print("=== Quick Throughput Test ===")
    num_messages = 5000

    start = time()
    for i in range(num_messages):
        await client.send_message(
            StreamType.MOUSE,
            x=i, y=i, event="move", dx=1, dy=1
        )
    elapsed = time() - start

    throughput = num_messages / elapsed

    print(f"Messages:     {num_messages}")
    print(f"Time:         {elapsed:.3f}s")
    print(f"Throughput:   {throughput:.0f} msg/s")
    print()

    print("="*70)
    print("✅ Quick benchmark complete!")
    print("="*70)

    # Cleanup
    await client.stop()
    await server.stop()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--quick":
        asyncio.run(benchmark_quick())
    else:
        asyncio.run(benchmark_server_client())

