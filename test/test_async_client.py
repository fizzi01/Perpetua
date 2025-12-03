"""
Test suite for AsyncClientConnectionHandler
"""
import asyncio
from network.connection.AsyncClientConnectionService import AsyncClientConnectionHandler
from network.connection.AsyncServerConnectionService import AsyncServerConnectionHandler
from model.ClientObj import ClientObj, ClientsManager
from utils.logging import Logger


async def test_client_connect_disconnect():
    """Test basic connection and disconnection"""
    print("=== Test Client Connect/Disconnect ===\n")

    Logger(stdout=print, logging=True)

    # Setup server
    server_clients = ClientsManager()
    test_client = ClientObj(
        ip_address="127.0.0.1",
        screen_position="right",
        screen_resolution="1920x1080"
    )
    server_clients.add_client(test_client)

    server_connected = []
    server_disconnected = []

    async def server_on_connected(client):
        server_connected.append(client)
        print(f"Server: Client {client.ip_address} connected")

    async def server_on_disconnected(client):
        server_disconnected.append(client)
        print(f"Server: Client {client.ip_address} disconnected")

    server = AsyncServerConnectionHandler(
        connected_callback=server_on_connected,
        disconnected_callback=server_on_disconnected,
        host="127.0.0.1",
        port=25001,
        heartbeat_interval=2,
        whitelist=server_clients
    )

    # Setup client
    client_connected = []
    client_disconnected = []

    async def client_on_connected(client):
        client_connected.append(client)
        print(f"Client: Connected to server as {client.screen_position}")

    async def client_on_disconnected(client):
        client_disconnected.append(client)
        print(f"Client: Disconnected from server")

    client = AsyncClientConnectionHandler(
        connected_callback=client_on_connected,
        disconnected_callback=client_on_disconnected,
        host="127.0.0.1",
        port=25001,
        wait=2,
        heartbeat_interval=2,
        max_errors=5,
        auto_reconnect=False,
        open_streams=[]  # No additional streams for this test
    )

    # Start server
    print("Starting server...")
    server_started = await server.start()
    assert server_started, "Server failed to start"
    print("✓ Server started\n")

    await asyncio.sleep(0.5)

    # Start client
    print("Starting client...")
    client_started = await client.start()
    assert client_started, "Client failed to start"
    print("✓ Client started\n")

    # Wait for connection
    print("Waiting for connection...")
    await asyncio.sleep(3)

    # Verify connection
    assert client.is_connected(), "Client not connected"
    assert len(client_connected) == 1, f"Expected 1 client connection, got {len(client_connected)}"
    assert len(server_connected) == 1, f"Expected 1 server connection, got {len(server_connected)}"
    print("✓ Connection established\n")

    # Stop client
    print("Stopping client...")
    await client.stop()
    print("✓ Client stopped\n")

    await asyncio.sleep(1)

    # Verify disconnection
    assert not client.is_connected(), "Client still connected"
    print("✓ Client disconnected\n")

    # Stop server
    print("Stopping server...")
    await server.stop()
    print("✓ Server stopped\n")

    print("✅ Test Client Connect/Disconnect PASSED\n")


async def test_client_auto_reconnect():
    """Test automatic reconnection"""
    print("=== Test Client Auto-Reconnect ===\n")

    Logger(stdout=print, logging=True)

    # Setup server
    server_clients = ClientsManager()
    test_client = ClientObj(
        ip_address="127.0.0.1",
        screen_position="left",
        screen_resolution="1920x1080"
    )
    server_clients.add_client(test_client)

    connection_count = [0]

    async def on_connected(client):
        connection_count[0] += 1
        print(f"Connection #{connection_count[0]}: Client {client.ip_address} connected")

    server = AsyncServerConnectionHandler(
        connected_callback=on_connected,
        host="127.0.0.1",
        port=25002,
        heartbeat_interval=1,
        whitelist=server_clients
    )

    # Setup client with auto-reconnect
    client = AsyncClientConnectionHandler(
        host="127.0.0.1",
        port=25002,
        wait=2,
        heartbeat_interval=1,
        auto_reconnect=True,
        open_streams=[]
    )

    # Start server
    await server.start()
    await asyncio.sleep(0.5)

    # Start client
    await client.start()
    await asyncio.sleep(2)

    assert client.is_connected(), "Client not connected initially"
    print(f"✓ Initial connection established (count: {connection_count[0]})\n")

    # Simulate server restart
    print("Simulating server restart...")
    await server.stop()
    await asyncio.sleep(3)

    assert not client.is_connected(), "Client still connected after server stop"
    print("✓ Client detected server down\n")

    # Restart server
    print("Restarting server...")
    await server.start()
    await asyncio.sleep(0.5)

    # Wait for auto-reconnect
    print("Waiting for auto-reconnect...")
    await asyncio.sleep(5)

    # Verify reconnection
    assert client.is_connected(), "Client failed to reconnect"
    assert connection_count[0] >= 2, f"Expected at least 2 connections, got {connection_count[0]}"
    print(f"✓ Auto-reconnect successful (total connections: {connection_count[0]})\n")

    # Cleanup
    await client.stop()
    await server.stop()

    print("✅ Test Client Auto-Reconnect PASSED\n")


async def test_client_connection_timeout():
    """Test connection timeout when server is not available"""
    print("=== Test Client Connection Timeout ===\n")

    Logger(stdout=print, logging=True)

    # Client connecting to non-existent server
    client = AsyncClientConnectionHandler(
        host="127.0.0.1",
        port=25003,  # No server listening
        wait=1,
        max_errors=3,
        auto_reconnect=False,
        open_streams=[]
    )

    print("Starting client (no server available)...")
    await client.start()

    # Wait for max_errors attempts
    print("Waiting for connection attempts...")
    await asyncio.sleep(5)

    # Should not be connected
    assert not client.is_connected(), "Client should not be connected"
    print("✓ Client correctly failed to connect\n")

    # Cleanup
    await client.stop()

    print("✅ Test Client Connection Timeout PASSED\n")


async def test_callback_compatibility():
    """Test sync and async callback compatibility"""
    print("=== Test Callback Compatibility ===\n")

    Logger(stdout=print, logging=True)

    # Setup server
    server_clients = ClientsManager()
    test_client = ClientObj(
        ip_address="127.0.0.1",
        screen_position="center",
        screen_resolution="1920x1080"
    )
    server_clients.add_client(test_client)

    sync_called = []
    async_called = []

    # Sync callback
    def sync_callback(client):
        sync_called.append(client)
        print(f"✓ Sync callback called for {client.ip_address}")

    # Async callback
    async def async_callback(client):
        async_called.append(client)
        print(f"✓ Async callback called for {client.ip_address}")

    server = AsyncServerConnectionHandler(
        host="127.0.0.1",
        port=25004,
        whitelist=server_clients
    )

    # Test with async callback
    client1 = AsyncClientConnectionHandler(
        connected_callback=async_callback,
        host="127.0.0.1",
        port=25004,
        auto_reconnect=False,
        open_streams=[]
    )

    await server.start()
    await asyncio.sleep(0.5)

    await client1.start()
    await asyncio.sleep(2)

    assert len(async_called) == 1, "Async callback not called"
    print("✓ Async callback works\n")

    await client1.stop()
    await asyncio.sleep(5)

    # Test with sync callback
    client2 = AsyncClientConnectionHandler(
        connected_callback=sync_callback,
        host="127.0.0.1",
        port=25004,
        auto_reconnect=False,
        open_streams=[]
    )

    await client2.start()
    await asyncio.sleep(5)

    assert len(sync_called) == 1, "Sync callback not called"
    print("✓ Sync callback works\n")

    # Cleanup
    await client2.stop()
    await server.stop()

    print("✅ Test Callback Compatibility PASSED\n")


async def main():
    """Run all tests"""
    print("="*70)
    print("AsyncClientConnectionHandler Test Suite")
    print("="*70 + "\n")

    try:
        await test_client_connect_disconnect()
        await test_client_auto_reconnect()
        await test_client_connection_timeout()
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

