"""
Unit tests for ServerConnectionHandler class.
Emulates server-client interactions using mock sockets and clients with multi-stream support.
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
import socket
import ssl
from itertools import chain, repeat
from time import sleep

from network.connection.ServerConnectionServices import ServerConnectionHandler
from network.connection.GeneralSocket import BaseSocket
from network.stream import StreamType
from model.ClientObj import ClientsManager, ClientObj
from network.data.MessageExchange import MessageExchange
from network.protocol.message import ProtocolMessage, MessageType
from utils.logging import Logger


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

    @patch('network.connection.ServerConnectionServices.BaseSocket')
    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_client_connection_successful_handshake_multistream(self, mock_server_socket, mock_base_socket):
        """Test successful client connection with handshake and multiple streams."""
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        # Mock primary client socket (COMMAND stream)
        mock_command_socket = MockSocket()
        client_addr = ("192.168.1.100", 12345)

        # Mock additional stream sockets
        mock_mouse_socket = MockSocket()
        mock_keyboard_socket = MockSocket()

        mouse_addr = ("192.168.1.100", 12346)
        keyboard_addr = ("192.168.1.100", 12347)

        # Create handshake response with requested streams
        handshake_response = ProtocolMessage(
            message_type=MessageType.EXCHANGE,
            timestamp=0,
            sequence_id=1,
            payload={
                "ack": True,
                "screen_resolution": "1920x1080",
                "additional_params": {"os": "linux"},
                "ssl": False,
                "streams": [StreamType.MOUSE, StreamType.KEYBOARD]  # Client requests additional streams
            },
            source="client",
            target="server"
        )

        # Configure mock message exchange
        self.mock_msg_exchange.receive_message.return_value = handshake_response

        # Mock BaseSocket instance
        mock_base_socket_instance = MagicMock()
        mock_base_socket.return_value = mock_base_socket_instance
        mock_base_socket_instance.put_stream.return_value = mock_base_socket_instance

        # Configure server to accept connections: command, mouse, keyboard, then timeout
        mock_server_instance.accept.side_effect = chain(
            [(mock_command_socket, client_addr)],  # Initial COMMAND connection
            [(mock_mouse_socket, mouse_addr)],     # MOUSE stream
            [(mock_keyboard_socket, keyboard_addr)],  # KEYBOARD stream
            repeat(socket.timeout())
        )

        handler = ServerConnectionHandler(
            msg_exchange=self.mock_msg_exchange,
            whitelist=self.clients_manager
        )
        handler.initialize()
        handler.start()

        sleep(0.7)  # Wait for connection and stream processing

        # Verify handshake was sent
        self.mock_msg_exchange.send_handshake_message.assert_called_once()

        # Verify BaseSocket was created with correct stream
        mock_base_socket.assert_called_once_with(client_addr)

        # Verify all streams were added
        calls = mock_base_socket_instance.put_stream.call_args_list
        self.assertEqual(len(calls), 3)  # COMMAND + MOUSE + KEYBOARD

        # Verify stream types
        stream_types = [call[0][0] for call in calls]
        self.assertIn(StreamType.COMMAND, stream_types)
        self.assertIn(StreamType.MOUSE, stream_types)
        self.assertIn(StreamType.KEYBOARD, stream_types)

        # Verify client was updated
        updated_client = self.clients_manager.get_client(ip_address="192.168.1.100")
        self.assertTrue(updated_client.is_connected)
        self.assertIsNotNone(updated_client.conn_socket)
        self.assertEqual(updated_client.screen_resolution, "1920x1080")

        # Verify ports were stored
        self.assertIn(StreamType.MOUSE, updated_client.ports)
        self.assertIn(StreamType.KEYBOARD, updated_client.ports)

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

    @patch('network.connection.ServerConnectionServices.BaseSocket')
    @patch('network.connection.ServerConnectionServices.ssl.create_default_context')
    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_ssl_multistream_connection(self, mock_server_socket, mock_ssl_context, mock_base_socket):
        """Test SSL connection wrapping for multiple streams."""
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        # Setup SSL mocks
        mock_context = Mock()
        mock_ssl_context.return_value = mock_context

        # Mock SSL wrapped sockets
        mock_ssl_command = Mock()
        mock_ssl_mouse = Mock()
        mock_context.wrap_socket.side_effect = [mock_ssl_command, mock_ssl_mouse]

        # Mock raw sockets
        mock_command_socket = MockSocket()
        mock_mouse_socket = MockSocket()

        client_addr = ("192.168.1.101", 12345)
        mouse_addr = ("192.168.1.101", 12346)

        # Handshake response for SSL client with streams
        handshake_response = ProtocolMessage(
            message_type=MessageType.EXCHANGE,
            timestamp=0,
            sequence_id=1,
            payload={
                "ack": True,
                "screen_resolution": "2560x1440",
                "ssl": True,
                "streams": [StreamType.MOUSE]
            },
            source="client",
            target="server"
        )

        self.mock_msg_exchange.receive_message.return_value = handshake_response

        # Mock BaseSocket
        mock_base_socket_instance = MagicMock()
        mock_base_socket.return_value = mock_base_socket_instance
        mock_base_socket_instance.put_stream.return_value = mock_base_socket_instance

        # Configure server accepts
        mock_server_instance.accept.side_effect = chain(
            [(mock_command_socket, client_addr)],
            [(mock_mouse_socket, mouse_addr)],
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

        sleep(0.7)

        # Verify SSL context was created
        mock_ssl_context.assert_called_once_with(ssl.Purpose.CLIENT_AUTH)
        mock_context.load_cert_chain.assert_called_once_with(
            certfile="cert.pem",
            keyfile="key.pem"
        )

        # Verify both sockets were wrapped with SSL (COMMAND is NOT wrapped, only additional streams)
        self.assertEqual(mock_context.wrap_socket.call_count, 1)  # Only MOUSE stream wrapped

        # Verify streams were added to BaseSocket
        calls = mock_base_socket_instance.put_stream.call_args_list
        self.assertEqual(len(calls), 2)  # COMMAND + MOUSE

        handler.stop()

    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_heartbeat_detection(self, mock_server_socket):
        """Test heartbeat loop detects disconnected clients."""
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        # Setup connected client with BaseSocket
        mock_base_socket = Mock(spec=BaseSocket)
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

    @patch('network.connection.ServerConnectionServices.BaseSocket')
    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_multiple_clients_multistream_connection(self, mock_server_socket, mock_base_socket):
        """Test handling multiple client connections with different stream configurations."""
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        # Client 1: COMMAND + MOUSE
        mock_client1_command = MockSocket()
        mock_client1_mouse = MockSocket()
        addr1_command = ("192.168.1.100", 12345)
        addr1_mouse = ("192.168.1.100", 12346)

        # Client 2: COMMAND + KEYBOARD + CLIPBOARD
        mock_client2_command = MockSocket()
        mock_client2_keyboard = MockSocket()
        mock_client2_clipboard = MockSocket()
        addr2_command = ("192.168.1.101", 12347)
        addr2_keyboard = ("192.168.1.101", 12348)
        addr2_clipboard = ("192.168.1.101", 12349)

        # Handshake responses
        handshake1 = ProtocolMessage(
            message_type=MessageType.EXCHANGE,
            timestamp=0,
            sequence_id=1,
            payload={
                "ack": True,
                "ssl": False,
                "screen_resolution": "1920x1080",
                "streams": [StreamType.MOUSE]
            },
            source="client"
        )

        handshake2 = ProtocolMessage(
            message_type=MessageType.EXCHANGE,
            timestamp=0,
            sequence_id=2,
            payload={
                "ack": True,
                "ssl": False,
                "screen_resolution": "2560x1440",
                "streams": [StreamType.KEYBOARD, StreamType.CLIPBOARD]
            },
            source="client"
        )

        self.mock_msg_exchange.receive_message.side_effect = [handshake1, handshake2]

        # Mock BaseSocket instances
        mock_base1 = MagicMock()
        mock_base2 = MagicMock()
        mock_base1.put_stream.return_value = mock_base1
        mock_base2.put_stream.return_value = mock_base2
        mock_base_socket.side_effect = [mock_base1, mock_base2]

        # Configure server accepts in order
        mock_server_instance.accept.side_effect = chain(
            # Client 1
            [(mock_client1_command, addr1_command)],
            [(mock_client1_mouse, addr1_mouse)],
            # Client 2
            [(mock_client2_command, addr2_command)],
            [(mock_client2_keyboard, addr2_keyboard)],
            [(mock_client2_clipboard, addr2_clipboard)],
            repeat(socket.timeout())
        )

        handler = ServerConnectionHandler(
            msg_exchange=self.mock_msg_exchange,
            whitelist=self.clients_manager
        )
        handler.initialize()
        handler.start()

        sleep(1.0)  # Wait for all connections

        # Verify both clients are connected
        client1 = self.clients_manager.get_client(ip_address="192.168.1.100")
        client2 = self.clients_manager.get_client(ip_address="192.168.1.101")

        self.assertTrue(client1.is_connected)
        self.assertTrue(client2.is_connected)

        # Verify screen resolutions
        self.assertEqual(client1.screen_resolution, "1920x1080")
        self.assertEqual(client2.screen_resolution, "2560x1440")

        # Verify stream counts
        self.assertEqual(len(mock_base1.put_stream.call_args_list), 2)  # COMMAND + MOUSE
        self.assertEqual(len(mock_base2.put_stream.call_args_list), 3)  # COMMAND + KEYBOARD + CLIPBOARD

        # Verify ports were stored
        self.assertIn(StreamType.MOUSE, client1.ports)
        self.assertIn(StreamType.KEYBOARD, client2.ports)
        self.assertIn(StreamType.CLIPBOARD, client2.ports)

        handler.stop()

    @patch('network.connection.ServerConnectionServices.ServerSocket')
    def test_stream_connection_wrong_client_rejection(self, mock_server_socket):
        """Test rejection of stream connection from wrong client IP during handshake."""
        mock_server_instance = Mock()
        mock_server_socket.return_value = mock_server_instance

        mock_command_socket = MockSocket()
        mock_wrong_stream = Mock()  # Stream from different IP

        client_addr = ("192.168.1.100", 12345)
        wrong_addr = ("192.168.1.200", 12346)  # Wrong IP

        handshake_response = ProtocolMessage(
            message_type=MessageType.EXCHANGE,
            timestamp=0,
            sequence_id=1,
            payload={
                "ack": True,
                "ssl": False,
                "streams": [StreamType.MOUSE]
            },
            source="client"
        )

        self.mock_msg_exchange.receive_message.return_value = handshake_response

        # Server accepts: command from correct client, then stream from wrong IP
        mock_server_instance.accept.side_effect = chain(
            [(mock_command_socket, client_addr)],
            [(mock_wrong_stream, wrong_addr)],  # Wrong IP
            repeat(socket.timeout())
        )

        handler = ServerConnectionHandler(
            msg_exchange=self.mock_msg_exchange,
            whitelist=self.clients_manager
        )
        handler.initialize()
        handler.start()

        sleep(0.7)

        # Verify wrong stream was closed
        mock_wrong_stream.close.assert_called_once()

        handler.stop()


if __name__ == '__main__':
    unittest.main()
