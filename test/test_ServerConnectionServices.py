"""
Unit tests for ServerConnectionHandler class.
Emulates server-client interactions using mock sockets and clients.
"""
import unittest
from unittest.mock import Mock, patch
import socket
import ssl
from itertools import chain, repeat
from time import sleep

from network.connection.ServerConnectionServices import ServerConnectionHandler
from model.ClientObj import ClientsManager, ClientObj
from network.data.MessageExchange import MessageExchange
from network.protocol.message import ProtocolMessage, MessageType
from utils.logging.logger import Logger


class StopException(Exception):
    """Custom exception to signal stopping the server connection handler."""
    pass


class MockSocket:
    """Mock socket for testing."""

    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.closed = False
        self.data_sent = []
        self.data_to_receive = []

    def send(self, data):
        if self.should_fail:
            raise ConnectionError("Mock send failure")
        self.data_sent.append(data)
        return len(data)

    def recv(self, bufsize):
        if self.should_fail:
            raise ConnectionError("Mock recv failure")
        if self.data_to_receive:
            return self.data_to_receive.pop(0)
        return b""

    def close(self):
        self.closed = True

    def getpeername(self):
        if self.should_fail:
            raise socket.error("Mock getpeername failure")
        return "Test"

    def settimeout(self, timeout):
        pass



class TestServerConnectionHandler(unittest.TestCase):
    """Test suite for ServerConnectionHandler."""

    def setUp(self):
        """Set up test fixtures."""
        # Init logger
        logger = Logger(logging=True, stdout=print)

        # Create test clients
        self.test_client1 = ClientObj(
            ip_address="192.168.1.100",
            screen_position="left",
            screen_resolution="1920x1080",
            ssl=False
        )

        self.test_client2 = ClientObj(
            ip_address="192.168.1.101",
            screen_position="right",
            screen_resolution="2560x1440",
            ssl=True
        )

        # Create client manager with whitelist
        self.clients_manager = ClientsManager()
        self.clients_manager.add_client(self.test_client1)
        self.clients_manager.add_client(self.test_client2)

        # Mock MessageExchange
        self.mock_msg_exchange = Mock(spec=MessageExchange)

    def tearDown(self):
        """Clean up after tests."""
        if hasattr(self, 'handler') and self.handler._running:
            self.handler.stop()

    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_initialization(self, mock_server_socket):
        """Test handler initialization."""
        handler = ServerConnectionHandler(
            msg_exchange=self.mock_msg_exchange,
            host="127.0.0.1",
            port=5001,
            whitelist=self.clients_manager
        )

        self.assertFalse(handler._initialized)
        self.assertFalse(handler._running)

        handler.initialize()

        self.assertTrue(handler._initialized)
        mock_server_socket.assert_called_once_with("127.0.0.1", 5001, 5)

    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_start_and_stop(self, mock_server_socket):
        """Test starting and stopping the handler."""
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        # Make accept block to simulate waiting
        mock_server_instance.accept.side_effect = socket.timeout()

        handler = ServerConnectionHandler(
            msg_exchange=self.mock_msg_exchange,
            whitelist=self.clients_manager,
            heartbeat_interval=1
        )
        handler.initialize()

        # Start handler
        result = handler.start()
        self.assertTrue(result)
        self.assertTrue(handler._running)

        sleep(0.2)  # Let threads start

        # Stop handler
        handler.stop()
        self.assertFalse(handler._running)

    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_client_connection_successful_handshake(self, mock_server_socket):
        """Test successful client connection with handshake."""
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        # Mock client socket
        mock_client_socket = MockSocket()
        client_addr = ("192.168.1.100", 12345)

        # Create handshake response
        handshake_response = ProtocolMessage(
            message_type=MessageType.EXCHANGE,
            timestamp=0,
            sequence_id=1,
            payload={
                "ack": True,
                "screen_resolution": "1920x1080",
                "additional_params": {"os": "linux"},
                "ssl": False
            },
            source="client",
            target="server"
        )

        # Configure mock message exchange
        self.mock_msg_exchange.receive_message.return_value = handshake_response

        # Configure server to accept one connection then timeout
        mock_server_instance.accept.side_effect = chain(
            [(mock_client_socket, client_addr)],
            repeat(socket.timeout())
        )

        handler = ServerConnectionHandler(
            msg_exchange=self.mock_msg_exchange,
            whitelist=self.clients_manager
        )
        handler.initialize()
        handler.start()

        sleep(0.5)  # Wait for connection processing

        # Verify handshake was sent
        self.mock_msg_exchange.send_handshake_message.assert_called_once()

        # Verify client was updated
        updated_client = self.clients_manager.get_client(ip_address="192.168.1.100")
        self.assertTrue(updated_client.is_connected)
        self.assertIsNotNone(updated_client.conn_socket)
        assertEquals(updated_client.screen_resolution, "1920x1080")

        handler.stop()

    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_client_connection_failed_handshake(self, mock_server_socket):
        """Test client connection with failed handshake."""
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        mock_client_socket = MockSocket()
        client_addr = ("192.168.1.100", 12345)

        # Handshake returns None (failed)
        self.mock_msg_exchange.receive_message.return_value = None

        mock_server_instance.accept.side_effect = chain(
            [(mock_client_socket, client_addr)],
            repeat(socket.timeout())
        )

        handler = ServerConnectionHandler(
            msg_exchange=self.mock_msg_exchange,
            whitelist=self.clients_manager
        )
        handler.initialize()
        handler.start()

        sleep(0.5)

        # Verify socket was closed
        self.assertTrue(mock_client_socket.closed)

        # Verify client is not connected
        client = self.clients_manager.get_client(ip_address="192.168.1.100")
        self.assertFalse(client.is_connected)

        handler.stop()

    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_client_not_in_whitelist(self, mock_server_socket):
        """Test connection rejection for non-whitelisted client."""
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        mock_client_socket = Mock()
        # Client not in whitelist
        client_addr = ("192.168.1.200", 12345)

        mock_server_instance.accept.side_effect = chain(
            [(mock_client_socket, client_addr)],
            repeat(socket.timeout())
        )

        handler = ServerConnectionHandler(
            msg_exchange=self.mock_msg_exchange,
            whitelist=self.clients_manager
        )
        handler.initialize()
        handler.start()

        sleep(0.5)

        # Verify socket was closed
        mock_client_socket.close.assert_called_once()

        # Verify handshake was NOT attempted
        self.mock_msg_exchange.send_handshake_message.assert_not_called()

        handler.stop()

    @patch('network.connection.ServerConnectionServices.ssl.create_default_context')
    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_ssl_connection(self, mock_server_socket, mock_ssl_context):
        """Test SSL connection wrapping."""
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        # Setup SSL mocks
        mock_context = Mock()
        mock_ssl_context.return_value = mock_context
        mock_ssl_socket = Mock()
        mock_context.wrap_socket.return_value = mock_ssl_socket

        mock_client_socket = MockSocket()
        client_addr = ("192.168.1.101", 12345)  # SSL client

        # Handshake response for SSL client
        handshake_response = ProtocolMessage(
            message_type=MessageType.EXCHANGE,
            timestamp=0,
            sequence_id=1,
            payload={
                "ack": True,
                "screen_resolution": "2560x1440",
                "ssl": True
            },
            source="client",
            target="server"
        )

        self.mock_msg_exchange.receive_message.return_value = handshake_response

        mock_server_instance.accept.side_effect = chain(
            [(mock_client_socket, client_addr)],
            repeat(socket.timeout())
        )

        handler = ServerConnectionHandler(
            msg_exchange=self.mock_msg_exchange,
            whitelist=self.clients_manager,
            certfile="cert.pem",
            keyfile="key.pem"
        )
        handler.initialize()
        handler.start()

        sleep(0.5)

        # Verify SSL context was created
        mock_ssl_context.assert_called_once_with(ssl.Purpose.CLIENT_AUTH)
        mock_context.load_cert_chain.assert_called_once_with(
            certfile="cert.pem",
            keyfile="key.pem"
        )

        # Verify socket was wrapped
        mock_context.wrap_socket.assert_called_once()

        handler.stop()

    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_heartbeat_detection(self, mock_server_socket):
        """Test heartbeat loop detects disconnected clients."""
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        # Setup connected client
        mock_socket = Mock()
        mock_base_socket = Mock()
        mock_base_socket.is_socket_open.return_value = False  # Simulate disconnect

        self.test_client1.is_connected = True
        self.test_client1.conn_socket = mock_base_socket

        mock_server_instance.accept.side_effect = socket.timeout()

        handler = ServerConnectionHandler(
            msg_exchange=self.mock_msg_exchange,
            whitelist=self.clients_manager,
            heartbeat_interval=1
        )
        handler.initialize()
        handler.start()

        # Wait for heartbeat check
        sleep(1.5)

        # Verify client was marked as disconnected
        client = self.clients_manager.get_client(ip_address="192.168.1.100")
        self.assertFalse(client.is_connected)
        mock_base_socket.close.assert_called_once()

        handler.stop()

    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_multiple_clients_connection(self, mock_server_socket):
        """Test handling multiple client connections."""
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        # Mock two client connections
        mock_client1_socket = MockSocket()
        mock_client2_socket = MockSocket()

        addr1 = ("192.168.1.100", 12345)
        addr2 = ("192.168.1.101", 12346)

        # Handshake responses
        handshake1 = ProtocolMessage(
            message_type=MessageType.EXCHANGE,
            timestamp=0,
            sequence_id=1,
            payload={"ack": True, "ssl": False, "screen_resolution": "1920x1080"},
            source="client"
        )

        handshake2 = ProtocolMessage(
            message_type=MessageType.EXCHANGE,
            timestamp=0,
            sequence_id=2,
            payload={"ack": True, "ssl": False, "screen_resolution": "2560x1440"},
            source="client"
        )

        self.mock_msg_exchange.receive_message.side_effect = [handshake1, handshake2]

        mock_server_instance.accept.side_effect = chain(
            [(mock_client1_socket, addr1),
            (mock_client2_socket, addr2)],
            repeat(socket.timeout())
        )

        handler = ServerConnectionHandler(
            msg_exchange=self.mock_msg_exchange,
            whitelist=self.clients_manager
        )
        handler.initialize()
        handler.start()

        sleep(0.5)

        # Verify both clients are connected
        client1 = self.clients_manager.get_client(ip_address="192.168.1.100")
        client2 = self.clients_manager.get_client(ip_address="192.168.1.101")

        self.assertTrue(client1.is_connected)
        self.assertTrue(client2.is_connected)

        # Verify screen resolutions
        self.assertEqual(client1.screen_resolution, "1920x1080")
        self.assertEqual(client2.screen_resolution, "2560x1440")

        handler.stop()

    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_handshake_timeout(self, mock_server_socket):
        """Test handshake timeout handling."""
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        mock_client_socket = MockSocket()
        client_addr = ("192.168.1.100", 12345)

        # Simulate timeout in receive_message
        self.mock_msg_exchange.receive_message.return_value = None

        mock_server_instance.accept.side_effect = chain(
            [(mock_client_socket, client_addr)],
            repeat(socket.timeout())
        )

        handler = ServerConnectionHandler(
            msg_exchange=self.mock_msg_exchange,
            whitelist=self.clients_manager
        )
        handler.initialize()
        handler.start()

        sleep(0.5)

        # Verify socket was closed due to handshake failure
        self.assertTrue(mock_client_socket.closed)

        client = self.clients_manager.get_client(ip_address="192.168.1.100")
        self.assertFalse(client.is_connected)

        handler.stop()


class TestServerConnectionHandlerIntegration(unittest.TestCase):
    """Integration tests simulating complete server-client scenarios."""

    @patch('network.connection.ServerConnectionServices.ServerSocket')
    @patch('network.connection.ServerConnectionServices.MessageExchange')
    def test_full_connection_lifecycle(self, mock_msg_exchange_class, mock_server_socket):
        """Test complete connection lifecycle: connect, heartbeat, disconnect."""
        # Setup mocks
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        mock_exchange_instance = Mock()
        mock_msg_exchange_class.return_value = mock_exchange_instance

        # Create client
        client = ClientObj(
            ip_address="192.168.1.100",
            screen_position="center",
            ssl=False
        )
        clients_manager = ClientsManager()
        clients_manager.add_client(client)

        # Mock connection
        mock_socket = Mock()
        mock_base_socket = Mock()
        addr = ("192.168.1.100", 12345)

        # Handshake response
        handshake_response = ProtocolMessage(
            message_type=MessageType.EXCHANGE,
            timestamp=0,
            sequence_id=1,
            payload={"ack": True, "ssl": False},
            source="client"
        )

        mock_exchange_instance.receive_message.return_value = handshake_response

        # First accept for connection, then timeout
        mock_server_instance.accept.side_effect = chain([
            (mock_socket, addr)],
            repeat(socket.timeout())
        )

        handler = ServerConnectionHandler(
            whitelist=clients_manager,
            heartbeat_interval=1
        )
        handler.initialize()
        handler.start()

        sleep(0.5)

        # Verify connection established
        connected_client = clients_manager.get_client(ip_address="192.168.1.100")
        self.assertTrue(connected_client.is_connected)

        # Stop and verify cleanup
        handler.stop()
        self.assertFalse(handler._running)


if __name__ == '__main__':
    unittest.main()
