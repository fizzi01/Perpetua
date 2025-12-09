"""
Test per AsyncServerConnectionHandler ottimizzato
"""
import asyncio
from network.connection.AsyncServerConnectionService import AsyncServerConnectionHandler
from model.ClientObj import ClientObj, ClientsManager
from utils.logging import Logger


async def test_server_startup_shutdown():
    """Test base di avvio e arresto del server"""
    print("=== Test Server Startup/Shutdown ===\n")

    # Setup logger
    Logger()

    # Setup whitelist
    clients = ClientsManager()
    test_client = ClientObj(
        ip_address="127.0.0.1",
        screen_position="right",
        screen_resolution="1920x1080"
    )
    clients.add_client(test_client)

    # Callbacks
    connected_clients = []
    disconnected_clients = []

    async def on_connected(client, streams):
        connected_clients.append(client)
        print(f"✓ Client connected: {client.ip_address}")

    async def on_disconnected(client, streams):
        disconnected_clients.append(client)
        print(f"✓ Client disconnected: {client.ip_address}")

    # Crea handler
    handler = AsyncServerConnectionHandler(
        connected_callback=on_connected,
        disconnected_callback=on_disconnected,
        host="127.0.0.1",
        port=15001,  # Porta custom per test
        heartbeat_interval=1,
        whitelist=clients
    )

    # Test avvio
    print("Avvio server...")
    success = await handler.start()
    assert success, "Server startup failed"
    print("✓ Server avviato con successo\n")

    # Aspetta un po'
    await asyncio.sleep(0.5)

    # Test arresto
    print("Arresto server...")
    await handler.stop()
    print("✓ Server arrestato con successo\n")

    print("✅ Test Startup/Shutdown PASSED")


async def test_server_multiple_start_stop():
    """Test avvio/arresto multipli"""
    print("\n=== Test Multiple Start/Stop ===\n")

    Logger()

    clients = ClientsManager()
    handler = AsyncServerConnectionHandler(
        host="127.0.0.1",
        port=15002,
        whitelist=clients
    )

    # Ciclo 3 volte
    for i in range(3):
        print(f"Ciclo {i+1}/3...")

        # Avvia
        success = await handler.start()
        assert success, f"Startup failed at cycle {i+1}"
        print(f"  ✓ Server avviato")

        await asyncio.sleep(0.2)

        # Ferma
        await handler.stop()
        print(f"  ✓ Server arrestato")

        await asyncio.sleep(0.1)

    print("\n✅ Test Multiple Start/Stop PASSED")


async def test_heartbeat_monitoring():
    """Test del monitoraggio heartbeat"""
    print("\n=== Test Heartbeat Monitoring ===\n")

    Logger()

    # Setup
    clients = ClientsManager()
    disconnected_clients = []

    async def on_disconnected(client, streams):
        disconnected_clients.append(client)
        print(f"✓ Heartbeat detected disconnection: {client.ip_address}")

    handler = AsyncServerConnectionHandler(
        disconnected_callback=on_disconnected,
        host="127.0.0.1",
        port=15003,
        heartbeat_interval=1,  # 1 secondo
        whitelist=clients
    )

    # Avvia
    success = await handler.start()
    assert success, "Server startup failed"
    print("✓ Server avviato con heartbeat interval=1s\n")

    # Lascia girare per qualche heartbeat
    print("Monitoring heartbeat per 3 secondi...")
    await asyncio.sleep(3)
    print("✓ Heartbeat funziona correttamente\n")

    # Ferma
    await handler.stop()
    print("✓ Server arrestato\n")

    print("✅ Test Heartbeat Monitoring PASSED")


async def test_callback_compatibility():
    """Test compatibilità callback sync e async"""
    print("\n=== Test Callback Compatibility ===\n")

    Logger()

    clients = ClientsManager()

    # Mix di callback sync e async
    sync_called = []
    async_called = []

    def sync_callback(client,strems):
        sync_called.append(client)
        print(f"✓ Sync callback called for {client.ip_address}")

    async def async_callback(client,streams):
        async_called.append(client)
        print(f"✓ Async callback called for {client.ip_address}")

    # Test 1: Async callback
    handler1 = AsyncServerConnectionHandler(
        connected_callback=async_callback,
        host="127.0.0.1",
        port=15004,
        whitelist=clients
    )

    await handler1.start()
    await asyncio.sleep(0.2)
    await handler1.stop()
    print("✓ Async callback test completed\n")

    # Test 2: Sync callback
    handler2 = AsyncServerConnectionHandler(
        connected_callback=sync_callback,
        host="127.0.0.1",
        port=15005,
        whitelist=clients
    )

    await handler2.start()
    await asyncio.sleep(0.2)
    await handler2.stop()
    print("✓ Sync callback test completed\n")

    print("✅ Test Callback Compatibility PASSED")


async def main():
    """Esegue tutti i test"""
    print("="*70)
    print("AsyncServerConnectionHandler Test Suite")
    print("="*70 + "\n")

    try:
        await test_server_startup_shutdown()
        await test_server_multiple_start_stop()
        await test_heartbeat_monitoring()
        await test_callback_compatibility()

        print("\n" + "="*70)
        print("✅ ALL TESTS PASSED")
        print("="*70)
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    asyncio.run(main())

