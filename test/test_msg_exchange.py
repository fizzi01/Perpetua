import socket
import threading
import time
import unittest
from network.data.MessageExchange import MessageExchange, MessageExchangeConfig
from network.protocol.message import MessageBuilder, ProtocolMessage

from utils.logging import Logger

Logger = Logger(stdout=print, logging=True)

class TestTCPMessageExchange(unittest.TestCase):
    """Test MessageExchange con connessione TCP reale."""

    def setUp(self):
        """Setup server e client socket."""
        self.port = 12345
        self.server_socket = None
        self.client_socket = None
        self.server_conn = None
        self.received_messages = []

        # Start server in background
        self.server_thread = threading.Thread(target=self._start_server, daemon=True)
        self.server_thread.start()
        time.sleep(0.1)  # Wait for server to start

        # Connect client
        self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.client_socket.connect(('localhost', self.port))
        print("Client: Connected to server.")
        time.sleep(0.5)
        self._large_receive_event = threading.Event()

    def _start_server(self):
        """Start TCP server."""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(('localhost', self.port))
        self.server_socket.listen(1)
        self.server_conn, _ = self.server_socket.accept()
        print("Server: Client connected.")

    def tearDown(self):
        """Cleanup sockets."""
        if self.client_socket:
            self.client_socket.close()
        if self.server_conn:
            self.server_conn.close()
        if self.server_socket:
            self.server_socket.close()

    def test_fragmented_receive(self):
        """Test ricezione con dati TCP frammentati."""
        # Setup exchange sul lato server
        exchange = MessageExchange(MessageExchangeConfig(
            enable_ordering=False,
            auto_chunk=False
        ))

        # Handler per messaggi ricevuti
        def handle_mouse(msg: ProtocolMessage):
            self.received_messages.append(msg)

        exchange.register_handler("mouse", handle_mouse)
        exchange.set_transport(receive_callback=self.server_conn.recv)

        # Client invia messaggio in frammenti
        builder = MessageBuilder()
        message = builder.create_mouse_message(x=100, y=200, dx=5, dy=10, event="move")
        full_data = message.to_bytes()

        # Invia in 3 frammenti
        chunk_size = len(full_data) // 3
        self.client_socket.send(full_data[:chunk_size])
        time.sleep(0.05)
        self.client_socket.send(full_data[chunk_size:chunk_size * 2])
        time.sleep(0.05)
        self.client_socket.send(full_data[chunk_size * 2:])

        # Ricevi messaggio
        received = exchange.receive_message(instant=True)

        # Verifica
        self.assertIsNotNone(received)
        self.assertEqual(received.message_type, "mouse")
        self.assertEqual(received.payload["x"], 100)
        self.assertEqual(received.payload["y"], 200)

    def test_multiple_messages_in_buffer(self):
        """Test ricezione di messaggi multipli concatenati."""
        exchange = MessageExchange(MessageExchangeConfig(enable_ordering=False))
        exchange.set_transport(receive_callback=self.server_conn.recv)

        builder = MessageBuilder()
        msg1 = builder.create_mouse_message(x=10, y=20, event="click")
        msg2 = builder.create_keyboard_message(key="A", event="press")

        # Invia entrambi i messaggi insieme
        combined_data = msg1.to_bytes() + msg2.to_bytes()
        self.client_socket.send(combined_data)
        time.sleep(0.1)

        # Ricevi primo messaggio
        received1 = exchange.receive_message(instant=True)
        self.assertIsNotNone(received1)
        self.assertEqual(received1.message_type, "mouse")

        # Ricevi secondo messaggio (dovrebbe essere nel buffer)
        received2 = exchange.receive_message(instant=True)
        self.assertIsNotNone(received2)
        self.assertEqual(received2.message_type, "keyboard")

    def test_large_message_chunking(self):
        """Test messaggio grande che richiede chunking del protocollo."""
        exchange = MessageExchange(MessageExchangeConfig(
            max_chunk_size=512,
            auto_chunk=True,
            enable_ordering=False
        ))

        def handle_clipboard(msg: ProtocolMessage):
            print(msg.payload)
            self.received_messages.append(msg)
            self._large_receive_event.set()

        exchange.register_handler("clipboard", handle_clipboard)
        exchange.set_transport(
            send_callback=self.client_socket.send,
            receive_callback=self.server_conn.recv
        )

        # Invia messaggio grande
        large_content = "X" * 2000
        exchange.send_clipboard_data(large_content)

        # Server riceve tutti i chunk
        for _ in range(10):  # Max 10 chunk
            exchange.receive_message(instant=False)
            if self._large_receive_event.is_set():
                break
            time.sleep(0.1)

        time.sleep(0.5)  # Attendi processing

        # Verifica ricostruzione
        self.assertEqual(len(self.received_messages), 1)
        self.assertEqual(self.received_messages[0].payload["content"], large_content)


if __name__ == '__main__':
    unittest.main()
