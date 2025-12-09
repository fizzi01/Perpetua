"""
Test per verificare MessageExchange con asyncio

Questi test utilizzano asyncio.gather() per eseguire operazioni di send e receive
in parallelo, simulando un comportamento reale di client-server dove entrambe
le operazioni avvengono contemporaneamente.
"""
import asyncio
from time import time
from network.data.MessageExchange import MessageExchange, MessageExchangeConfig
from network.protocol.message import ProtocolMessage
from utils.logging import Logger
import logging

logging.basicConfig(level=logging.WARNING)  # Solo warning ed errori


async def test_basic_messaging():
    """Test base per invio e ricezione messaggi"""
    Logger()

    print("=== Test Basic Messaging ===")
    start_time = time()

    # Configurazione
    config = MessageExchangeConfig(
        max_chunk_size=1024,
        auto_chunk=True,
        auto_dispatch=False,
    )

    # Simula connessione con code asyncio ottimizzate
    server_to_client_queue = asyncio.Queue(maxsize=100)
    client_to_server_queue = asyncio.Queue(maxsize=100)

    # MessageExchange per server e client
    server_exchange = MessageExchange(config)
    client_exchange = MessageExchange(config)

    # Setup transport layer callbacks ottimizzati
    async def server_send(data: bytes):
        await server_to_client_queue.put(data)

    async def server_recv(size: int) -> bytes:
        data = await client_to_server_queue.get()
        return data

    async def client_send(data: bytes):
        await client_to_server_queue.put(data)

    async def client_recv(size: int) -> bytes:
        data = await server_to_client_queue.get()
        return data

    await server_exchange.set_transport(server_send, server_recv)
    await client_exchange.set_transport(client_send, client_recv)

    # Registra handlers sul server
    received_messages = []

    async def handle_mouse(msg: ProtocolMessage):
        received_messages.append(('mouse', msg.payload))
        print(f"✓ Received mouse event")

    async def handle_keyboard(msg: ProtocolMessage):
        received_messages.append(('keyboard', msg.payload))
        print(f"✓ Received keyboard event")

    server_exchange.register_handler("mouse", handle_mouse)
    server_exchange.register_handler("keyboard", handle_keyboard)

    # Avvia ricezione sul server
    await server_exchange.start()

    # Task per processare messaggi ricevuti (ottimizzato)
    async def process_server_messages():
        count = 0
        while count < 2:  # Processa esattamente 2 messaggi
            try:
                message = await server_exchange.get_received_message(timeout=0.01)
                if message:
                    await server_exchange.dispatch_message(message)
                    count += 1
            except asyncio.TimeoutError:
                break

    # Task per inviare messaggi dal client
    async def send_messages():
        print("\nInvio messaggi dal client...")
        await client_exchange.send_mouse_data(100, 200, "click", 0, 0, is_pressed=True)
        await client_exchange.send_keyboard_data("A", "press")

    # Usa gather per combinare send e receive in parallelo
    await asyncio.gather(
        process_server_messages(),
        send_messages()
    )

    # Verifica
    elapsed = time() - start_time
    print(f"\nMessaggi ricevuti: {len(received_messages)}")
    print(f"⏱️  Tempo esecuzione: {elapsed:.3f}s")
    assert len(received_messages) == 2, f"Expected 2 messages, got {len(received_messages)}"
    print("✓ Test Basic Messaging PASSED")

    # Cleanup
    await server_exchange.stop()
    return elapsed


async def test_chunked_messages():
    """Test per messaggi frammentati (chunked)"""
    print("\n=== Test Chunked Messages ===")
    start_time = time()

    # Configurazione con chunk size piccolo
    config = MessageExchangeConfig(
        max_chunk_size=1024,
        auto_chunk=True,
        auto_dispatch=False,
    )

    # Simula connessione
    server_to_client_queue = asyncio.Queue(maxsize=1000)
    client_to_server_queue = asyncio.Queue(maxsize=1000)

    server_exchange = MessageExchange(config)
    client_exchange = MessageExchange(config)

    async def server_send(data: bytes):
        await server_to_client_queue.put(data)

    async def server_recv(size: int) -> bytes:
        data = await client_to_server_queue.get()
        return data

    async def client_send(data: bytes):
        await client_to_server_queue.put(data)

    async def client_recv(size: int) -> bytes:
        data = await server_to_client_queue.get()
        return data

    await server_exchange.set_transport(server_send, server_recv)
    await client_exchange.set_transport(client_send, client_recv)

    # Handler per clipboard
    received_clipboard = []

    async def handle_clipboard(msg: ProtocolMessage):
        content = msg.payload.get('content', '')
        received_clipboard.append(content)
        print(f"✓ Received clipboard, length: {len(content)}")

    server_exchange.register_handler("clipboard", handle_clipboard)

    # Avvia ricezione
    await server_exchange.start()

    # Task per processare messaggi (ottimizzato)
    async def process_messages():
        while len(received_clipboard) == 0:
            try:
                message = await server_exchange.get_received_message(timeout=0.01)
                if message:
                    await server_exchange.dispatch_message(message)
            except asyncio.TimeoutError:
                break

    # Task per inviare contenuto grande
    async def send_large_content():
        print("\nInvio contenuto grande (500KB)...")
        large_content = "X" * 500000
        send_start = time()
        await client_exchange.send_clipboard_data(large_content)
        send_time = time() - send_start
        print(f"  Send time: {send_time:.3f}s")
        return send_time

    # Usa gather per combinare send e receive in parallelo
    results = await asyncio.gather(
        process_messages(),
        send_large_content()
    )
    send_time = results[1]

    # Verifica
    elapsed = time() - start_time
    print(f"\nContenuti clipboard ricevuti: {len(received_clipboard)}")
    print(f"⏱️  Tempo totale: {elapsed:.3f}s")
    print(f"📊 Throughput: {500000 / elapsed / 1024:.2f} KB/s")
    assert len(received_clipboard) == 1, f"Expected 1 message, got {len(received_clipboard)}"
    assert len(received_clipboard[0]) == 500000, f"Expected 500000 chars, got {len(received_clipboard[0])}"
    print("✓ Test Chunked Messages PASSED")

    # Cleanup
    await server_exchange.stop()
    return elapsed


async def test_rapid_fire():
    """Test per invio rapido di molti messaggi"""
    print("\n=== Test Rapid Fire Messages ===")
    start_time = time()

    config = MessageExchangeConfig(max_chunk_size=4096, auto_chunk=False, auto_dispatch=False)

    # Simula connessione
    server_to_client_queue = asyncio.Queue(maxsize=1000)
    client_to_server_queue = asyncio.Queue(maxsize=1000)

    server_exchange = MessageExchange(config)
    client_exchange = MessageExchange(config)

    async def server_send(data: bytes):
        await server_to_client_queue.put(data)

    async def server_recv(size: int) -> bytes:
        data = await client_to_server_queue.get()
        return data

    async def client_send(data: bytes):
        await client_to_server_queue.put(data)

    async def client_recv(size: int) -> bytes:
        data = await server_to_client_queue.get()
        return data

    await server_exchange.set_transport(server_send, server_recv)
    await client_exchange.set_transport(client_send, client_recv)

    # Handler
    mouse_events = []

    async def handle_mouse(msg: ProtocolMessage):
        mouse_events.append(msg.payload)

    server_exchange.register_handler("mouse", handle_mouse)

    # Avvia ricezione
    await server_exchange.start()

    # Task per processare messaggi (ottimizzato)
    num_messages = 100

    async def process_messages():
        while len(mouse_events) < num_messages:
            try:
                message = await server_exchange.get_received_message(timeout=0.01)
                if message:
                    await server_exchange.dispatch_message(message)
            except asyncio.TimeoutError:
                break

    # Task per inviare molti messaggi
    async def send_rapid_fire():
        print(f"\nInvio {num_messages} messaggi mouse...")
        send_start = time()
        for i in range(num_messages):
            await client_exchange.send_mouse_data(i, i, "move", 1, 1)
        send_time = time() - send_start
        print(f"  Send time: {send_time:.3f}s ({num_messages/send_time:.0f} msg/s)")
        return num_messages

    # Usa gather per combinare send e receive in parallelo
    results = await asyncio.gather(
        process_messages(),
        send_rapid_fire()
    )
    num_sent = results[1]

    # Verifica
    elapsed = time() - start_time
    print(f"\nEventi mouse ricevuti: {len(mouse_events)}/{num_sent}")
    print(f"⏱️  Tempo totale: {elapsed:.3f}s")
    print(f"📊 Throughput: {len(mouse_events)/elapsed:.0f} msg/s")
    assert len(mouse_events) == num_sent, f"Expected {num_sent}, got {len(mouse_events)}"
    print("✓ Test Rapid Fire Messages PASSED")

    # Cleanup
    await server_exchange.stop()
    return elapsed


async def main():
    """Esegue tutti i test con benchmark dettagliati"""
    print("="*70)
    print("MessageExchange AsyncIO Performance Benchmark")
    print("="*70)

    overall_start = time()
    times = {}

    try:
        # Test 1: Basic Messaging
        times['basic'] = await test_basic_messaging()

        # Test 2: Chunked Messages
        times['chunked'] = await test_chunked_messages()

        # Test 3: Rapid Fire
        times['rapid_fire'] = await test_rapid_fire()

        # Statistiche finali
        total_time = time() - overall_start

        print("\n" + "="*70)
        print("BENCHMARK RESULTS")
        print("="*70)
        print(f"Basic Messaging:      {times['basic']:.3f}s")
        print(f"Chunked Messages:     {times['chunked']:.3f}s")
        print(f"Rapid Fire (1000msg): {times['rapid_fire']:.3f}s")
        print(f"-" * 70)
        print(f"Total Time:           {total_time:.3f}s")
        print("="*70)
        print("✅ ALL TESTS PASSED")
        print("="*70)

    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    asyncio.run(main(), debug=False)

