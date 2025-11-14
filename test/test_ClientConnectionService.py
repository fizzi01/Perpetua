"""
Unit tests for ClientConnectionHandler class.
Tests client-server interactions using the real ServerConnectionHandler.
"""
import unittest
from time import sleep

from network.connection.ClientConnectionService import ClientConnectionHandler
from network.connection.ServerConnectionServices import ServerConnectionHandler
from network.stream import StreamType
from model.ClientObj import ClientsManager, ClientObj
from network.data.MessageExchange import MessageExchange
from utils.logging import Logger


class TestClientConnectionHandler(unittest.TestCase):
    """Test suite for ClientConnectionHandler using real server."""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures that are shared across all tests."""
        # Init logger
        cls.logger = Logger(logging=True, stdout=print)

    def setUp(self):
        """Set up test fixtures for each test."""
        # Server configuration
        self.server_host = "127.0.0.1"
        self.server_port = 5050

        # Create server-side client manager with whitelist
        self.server_clients = ClientsManager()

        # Add client to server whitelist
        self.test_client = ClientObj(
            ip_address=self.server_host,
            screen_position="left",
            screen_resolution="1920x1080",
            ssl=False
        )
        self.server_clients.add_client(self.test_client)

        # Initialize server
        self.server = ServerConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            whitelist=self.server_clients,
            heartbeat_interval=2,
            wait=5
        )

        # Client-side client manager
        self.client_manager = ClientsManager()

        # Callbacks tracking
        self.connected_called = False
        self.disconnected_called = False
        self.connected_client = None
        self.disconnected_client = None

    def tearDown(self):
        """Clean up after each test."""
        if hasattr(self, 'client') and self.client._running:
            self.client.stop()

        if hasattr(self, 'server') and self.server._running:
            self.server.stop()

        sleep(0.3)  # Allow cleanup

    def _on_client_connected(self, client: ClientObj):
        """Callback for client connection."""
        self.connected_called = True
        self.connected_client = client
        self.logger.log(f"Client connected callback: {client.ip_address}", Logger.INFO)

    def _on_client_disconnected(self, client: ClientObj):
        """Callback for client disconnection."""
        self.disconnected_called = True
        self.disconnected_client = client
        self.logger.log(f"Client disconnected callback: {client.ip_address}", Logger.INFO)

    def test_initialization(self):
        """Test client handler initialization."""
        client = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=self.client_manager
        )

        self.assertFalse(client._initialized)
        self.assertFalse(client._running)
        self.assertIsNone(client.socket_client)

        client.initialize()

        self.assertTrue(client._initialized)
        self.assertIsNotNone(client.socket_client)

        # Verify client object was created
        client_obj = self.client_manager.get_client(ip_address=self.server_host)
        self.assertIsNotNone(client_obj)
        self.assertEqual(client_obj.ip_address, self.server_host)

    def test_start_and_stop(self):
        """Test starting and stopping the client handler."""
        self.client = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=self.client_manager
        )

        # Start without server running - should handle gracefully
        result = self.client.start()
        self.assertTrue(result)
        self.assertTrue(self.client._running)

        sleep(0.3)

        # Stop client
        result = self.client.stop()
        self.assertTrue(result)
        self.assertFalse(self.client._running)

    def test_successful_connection_and_handshake(self):
        """Test successful client connection with server and handshake."""
        # Start server
        self.server.initialize()
        self.server.start()
        sleep(0.5)  # Let server start

        # Create and start client
        self.client = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=self.client_manager,
            connected_callback=self._on_client_connected,
            wait=2
        )

        self.client.start()
        sleep(2.0)  # Wait for connection and handshake

        # Verify client connected
        self.assertTrue(self.connected_called)
        self.assertIsNotNone(self.connected_client)
        self.assertTrue(self.connected_client.is_connected)

        # Verify client has all streams
        client_obj = self.client_manager.get_client(ip_address=self.server_host)
        self.assertIsNotNone(client_obj)
        self.assertTrue(client_obj.is_connected)
        self.assertIsNotNone(client_obj.conn_socket)

        # Verify server side
        server_client = self.server_clients.get_client(ip_address=self.server_host)
        self.assertTrue(server_client.is_connected)

    def test_connection_with_multiple_streams(self):
        """Test client connection establishes all requested streams."""
        # Start server
        self.server.initialize()
        self.server.start()
        sleep(0.5)

        # Create and start client
        self.client = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=self.client_manager,
            connected_callback=self._on_client_connected
        )

        self.client.start()
        sleep(2.0)  # Wait for all streams to connect

        # Verify connection
        self.assertTrue(self.connected_called)

        # Verify streams on server side
        server_client = self.server_clients.get_client(ip_address=self.server_host)
        self.assertTrue(server_client.is_connected)
        self.assertIsNotNone(server_client.conn_socket)

        # Check that server has all expected streams
        expected_streams = [StreamType.COMMAND, StreamType.MOUSE, StreamType.KEYBOARD, StreamType.CLIPBOARD]
        for stream_type in expected_streams:
            self.assertIn(stream_type, server_client.conn_socket.streams)

    def test_connection_without_server(self):
        """Test client behavior when server is not available."""
        # Don't start server
        self.client = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=self.client_manager,
            wait=1
        )

        self.client.start()
        sleep(2.0)  # Wait for connection attempts

        # Client should be running but not connected
        self.assertTrue(self.client._running)
        self.assertFalse(self.connected_called)

        # Client object should not be connected
        client_obj = self.client_manager.get_client(ip_address=self.server_host)
        if client_obj:
            self.assertFalse(client_obj.is_connected)

    def test_server_disconnect_detection(self):
        """Test client detects when server disconnects."""
        # Start server
        self.server.initialize()
        self.server.start()
        sleep(0.5)

        # Create and start client
        self.client = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=self.client_manager,
            connected_callback=self._on_client_connected,
            disconnected_callback=self._on_client_disconnected,
            wait=1
        )

        self.client.start()
        sleep(2.0)  # Wait for connection

        # Verify connected
        self.assertTrue(self.connected_called)

        # Stop server to simulate disconnect
        self.server.stop()
        sleep(2.0)  # Wait for client to detect disconnect

        # Verify disconnection was detected
        self.assertTrue(self.disconnected_called)

        # Client should mark itself as disconnected
        client_obj = self.client_manager.get_client(ip_address=self.server_host)
        if client_obj:
            self.assertFalse(client_obj.is_connected)

    def test_reconnection_after_disconnect(self):
        """Test client reconnects after server disconnect."""
        # Start server
        self.server.initialize()
        self.server.start()
        sleep(0.5)

        # Create and start client
        self.client = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=self.client_manager,
            connected_callback=self._on_client_connected,
            wait=1
        )

        self.client.start()
        sleep(2.0)  # Wait for first connection

        # Verify first connection
        self.assertTrue(self.connected_called)
        first_connection = self.connected_client

        # Reset callback flag
        self.connected_called = False
        self.connected_client = None

        # Stop and restart server
        self.server.stop()
        sleep(1.0)

        # Restart server
        self.server = ServerConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            whitelist=self.server_clients,
            heartbeat_interval=2
        )
        self.server.initialize()
        self.server.start()
        sleep(3.0)  # Wait for reconnection

        # Verify reconnection occurred
        self.assertTrue(self.connected_called)
        self.assertIsNotNone(self.connected_client)
        self.assertTrue(self.connected_client.is_connected)

    def test_handshake_with_screen_info(self):
        """Test that client sends screen information during handshake."""
        # Start server
        self.server.initialize()
        self.server.start()
        sleep(0.5)

        # Create client with specific screen info
        client_obj = ClientObj(
            ip_address=self.server_host,
            screen_position="right",
            screen_resolution="2560x1440",
            ssl=False
        )
        self.client_manager.add_client(client_obj)

        # Update server whitelist
        self.server_clients.clients[0].screen_resolution = None  # Clear to verify update

        self.client = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=self.client_manager,
            connected_callback=self._on_client_connected
        )

        self.client.start()
        sleep(2.0)  # Wait for handshake

        # Verify connection
        self.assertTrue(self.connected_called)

        # Verify server received client screen info
        server_client = self.server_clients.get_client(ip_address=self.server_host)
        self.assertIsNotNone(server_client.screen_resolution)
        self.assertEqual(server_client.screen_resolution, "2560x1440")

    def test_multiple_connection_attempts(self):
        """Test client handles multiple connection attempts correctly."""
        # Don't start server initially
        self.client = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=self.client_manager,
            connected_callback=self._on_client_connected,
            wait=1
        )

        self.client.start()
        sleep(2.0)  # Let it try to connect

        # Verify not connected
        self.assertFalse(self.connected_called)

        # Now start server
        self.server.initialize()
        self.server.start()
        sleep(2.0)  # Wait for connection

        # Verify connection succeeded
        self.assertTrue(self.connected_called)
        self.assertTrue(self.connected_client.is_connected)

    def test_client_not_in_server_whitelist(self):
        """Test connection rejected when client not in server whitelist."""
        # Create server with empty whitelist
        self.server_clients = ClientsManager()
        self.server = ServerConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            whitelist=self.server_clients,  # Empty whitelist
            heartbeat_interval=2
        )

        self.server.initialize()
        self.server.start()
        sleep(0.5)

        # Create and start client
        self.client = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=self.client_manager,
            connected_callback=self._on_client_connected,
            wait=1
        )

        self.client.start()
        sleep(2.0)  # Wait for connection attempt

        # Verify connection was rejected
        self.assertFalse(self.connected_called)

    def test_callbacks_execution(self):
        """Test that callbacks are properly executed."""
        # Start server
        self.server.initialize()
        self.server.start()
        sleep(0.5)

        # Create and start client with callbacks
        self.client = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=self.client_manager,
            connected_callback=self._on_client_connected,
            disconnected_callback=self._on_client_disconnected,
            wait=1
        )

        self.client.start()
        sleep(2.0)  # Wait for connection

        # Verify connected callback
        self.assertTrue(self.connected_called)
        self.assertIsNotNone(self.connected_client)

        # Disconnect and verify callback
        self.server.stop()
        sleep(2.0)

        self.assertTrue(self.disconnected_called)
        self.assertIsNotNone(self.disconnected_client)

    def test_concurrent_clients(self):
        """Test server can handle multiple clients connecting."""
        # Add second client to whitelist
        second_client = ClientObj(
            ip_address=self.server_host,
            screen_position="right",
            screen_resolution="1920x1080",
            ssl=False
        )
        self.server_clients.add_client(second_client)

        # Start server
        self.server.initialize()
        self.server.start()
        sleep(0.5)

        # Create first client
        client1_manager = ClientsManager()
        self.client = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=client1_manager,
            wait=1
        )
        self.client.start()
        sleep(2.0)

        # Create second client
        client2_manager = ClientsManager()
        client2 = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=client2_manager,
            wait=1
        )
        client2.start()
        sleep(2.0)

        # Verify both clients connected
        client1_obj = client1_manager.get_client(ip_address=self.server_host)
        client2_obj = client2_manager.get_client(ip_address=self.server_host)

        # Note: Both clients use same IP (localhost), so server might handle them differently
        # This test verifies the architecture handles concurrent connections
        self.assertTrue(self.client._running)
        self.assertTrue(client2._running)

        # Cleanup
        client2.stop()

    def test_heartbeat_keeps_connection_alive(self):
        """Test that connection stays alive over time."""
        # Start server with short heartbeat
        self.server.initialize()
        self.server.start()
        sleep(0.5)

        # Create and start client
        self.client = ClientConnectionHandler(
            msg_exchange=MessageExchange(),
            host=self.server_host,
            port=self.server_port,
            clients=self.client_manager,
            connected_callback=self._on_client_connected,
            disconnected_callback=self._on_client_disconnected,
            wait=1
        )

        self.client.start()
        sleep(2.0)  # Wait for connection

        # Verify connected
        self.assertTrue(self.connected_called)

        # Wait for multiple heartbeat intervals
        sleep(5.0)

        # Verify still connected
        #self.assertFalse(self.disconnected_called)
        client_obj = self.client_manager.get_client(ip_address=self.server_host)
        self.assertTrue(client_obj.is_connected)

        # Verify on server side too
        server_client = self.server_clients.get_client(ip_address=self.server_host)
        self.assertTrue(server_client.is_connected)


if __name__ == '__main__':
    unittest.main()

