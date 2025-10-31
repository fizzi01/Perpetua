#!/usr/bin/env python3
"""
Test unittest per BidirectionalStreamHandler con CommandEvent.
Esegue test in sequenza con client esterno.
"""
import json
import os
import sys
import time
import unittest
import subprocess
from pathlib import Path

# Aggiungi il path del progetto
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from network.stream.CustomStream import BidirectionalStreamHandler
from network.connection.ServerConnectionServices import ServerConnectionHandler
from network.stream.StreamObj import StreamType
from model.ClientObj import ClientsManager, ClientObj
from event.EventBus import ThreadSafeEventBus
from event.Event import EventType, CommandEvent
from utils.logging.logger import Logger


class TestBidirectionalCommandStream(unittest.TestCase):
    """Test suite per BidirectionalStreamHandler con CommandEvent."""

    @classmethod
    def setUpClass(cls):
        """Setup iniziale eseguito una sola volta."""
        cls.logger = Logger(stdout=print, logging=True)
        cls.logger.set_level(Logger.DEBUG)
        cls.host = "127.0.0.1"
        cls.base_port = 5052
        cls.events_file = Path("test/temp/client_command_events.json")
        cls.events_file.parent.mkdir(parents=True, exist_ok=True)

        # Clear previous test data
        if cls.events_file.exists():
            cls.events_file.unlink()

        cls.event_bus = ThreadSafeEventBus()
        cls.clients_manager = ClientsManager()
        cls.client_process = None
        cls.received_commands = []

        cls._setup_server()
        cls._start_external_client()
        cls._setup_command_handler()

        cls.logger.log("\n✓ Setup completato\n", Logger.INFO)

    @classmethod
    def tearDownClass(cls):
        """Cleanup eseguito una sola volta."""
        cls.logger.log("\n" + "=" * 80, Logger.INFO)
        cls.logger.log("CLEANUP", Logger.INFO)
        cls.logger.log("=" * 80, Logger.INFO)

        if cls.command_handler:
            cls.command_handler.stop()

        time.sleep(1)

        if cls.server:
            cls.server.stop()

        time.sleep(1)

        if cls.client_process:
            cls.client_process.terminate()
            cls.client_process.wait(timeout=5)

        cls.logger.log("✓ Cleanup completato\n", Logger.INFO)

    @classmethod
    def _setup_server(cls):
        """Inizializza il server."""
        cls.logger.log("=" * 80, Logger.INFO)
        cls.logger.log("SETUP SERVER", Logger.INFO)
        cls.logger.log("=" * 80, Logger.INFO)

        test_client = ClientObj(
            ip_address=cls.host,
            screen_position="center",
            client_name="TestCommandClient",
            ssl=False
        )
        cls.clients_manager.add_client(test_client)

        cls.server = ServerConnectionHandler(
            host=cls.host,
            port=cls.base_port,
            wait=5,
            heartbeat_interval=30,
            whitelist=cls.clients_manager,
            connected_callback=lambda c: cls.logger.log(f"Client {c.client_name} connected", Logger.INFO),
            disconnected_callback=lambda c: cls.logger.log(f"Client {c.client_name} disconnected", Logger.WARNING)
        )

        cls.server.initialize()
        if not cls.server.start():
            raise RuntimeError("Failed to start server")

        cls.logger.log(f"✓ Server started on {cls.host}:{cls.base_port}", Logger.INFO)

    @classmethod
    def _start_external_client(cls):
        """Avvia il client esterno."""
        cls.logger.log("\n" + "=" * 80, Logger.INFO)
        cls.logger.log("STARTING EXTERNAL CLIENT", Logger.INFO)
        cls.logger.log("=" * 80, Logger.INFO)

        cls.client_process = subprocess.Popen([
            "python3", "standalone_test_client.py",
            "--host", cls.host,
            "--port", str(cls.base_port),
            "--position", "center",
            "--events-file", str(cls.events_file),
            "--stream-type", "command"
        ])

        # Wait for client connection
        cls.logger.log("Waiting for client connection...", Logger.INFO)
        timeout = time.time() + 60
        while time.time() < timeout:
            status = cls._read_client_status()
            if status.get("status") == "receiving":
                cls.logger.log("✓ Client connected and ready!", Logger.INFO)
                time.sleep(1)
                return
            time.sleep(2)

        raise RuntimeError("Client connection timeout")

    @classmethod
    def _setup_command_handler(cls):
        """Inizializza BidirectionalStreamHandler."""
        cls.logger.log("\n" + "=" * 80, Logger.INFO)
        cls.logger.log("SETUP BIDIRECTIONAL HANDLER", Logger.INFO)
        cls.logger.log("=" * 80, Logger.INFO)

        def command_handler(msg):
            """Handler per comandi ricevuti dal client."""
            cls.logger.log(f"Received command from client: {msg}", Logger.INFO)
            cls.received_commands.append(msg)

        cls.command_handler = BidirectionalStreamHandler(
            stream_type=StreamType.COMMAND,
            clients=cls.clients_manager,
            event_bus=cls.event_bus,
            handler_id="CommandStreamHandler",
            source="server",
            instant=False
        )

        cls.command_handler.register_receive_callback(command_handler, "command")
        cls.command_handler.start()

        # Activate client
        cls.event_bus.dispatch(
            EventType.ACTIVE_SCREEN_CHANGED,
            {"active_screen": "center"}
        )
        time.sleep(1)

        cls.logger.log("✓ Command handler initialized", Logger.INFO)

    @classmethod
    def _read_client_status(cls):
        """Legge lo stato corrente dal file eventi."""
        try:
            if not cls.events_file.exists():
                return {}
            with open(cls.events_file, 'r') as f:
                return json.load(f)
        except:
            return {}

    @classmethod
    def _get_client_event_count(cls):
        """Ottiene il numero di eventi ricevuti dal client."""
        status = cls._read_client_status()
        return status.get("count", 0)

    def test_01_send_basic_commands(self):
        """Test invio comandi base al client."""
        self.logger.log("\n" + "=" * 80, Logger.INFO)
        self.logger.log("TEST 1: Send basic commands to client", Logger.INFO)
        self.logger.log("=" * 80, Logger.INFO)

        test_commands = [
            CommandEvent("shutdown", {"timeout": 5}),
            CommandEvent("get_status", {}),
            CommandEvent("set_config", {"key": "resolution", "value": "1920x1080"}),
        ]

        initial_count = self._get_client_event_count()

        for cmd in test_commands:
            self.logger.log(f"Sending: {cmd.command} with params {cmd.params}", Logger.DEBUG)
            self.command_handler.send(cmd.to_dict())
            time.sleep(0.2)

        time.sleep(2)

        received_count = self._get_client_event_count() - initial_count
        self.logger.log(f"Client received {received_count}/{len(test_commands)} commands", Logger.INFO)

        self.assertGreater(received_count, 0, "Client should receive at least some commands")

    def test_02_high_frequency_commands(self):
        """Test comandi ad alta frequenza."""
        self.logger.log("\n" + "=" * 80, Logger.INFO)
        self.logger.log("TEST 2: High frequency command stream", Logger.INFO)
        self.logger.log("=" * 80, Logger.INFO)

        initial_count = self._get_client_event_count()
        event_count = 50

        for i in range(event_count):
            cmd = CommandEvent("update", {"index": i, "timestamp": time.time()})
            self.command_handler.send(cmd.to_dict())
            time.sleep(0.02)

        time.sleep(2)

        received_count = self._get_client_event_count() - initial_count
        reception_rate = (received_count / event_count) * 100
        self.logger.log(f"Received {received_count}/{event_count} commands ({reception_rate:.1f}%)", Logger.INFO)

        self.assertGreater(reception_rate, 50, "Should receive at least 50% of commands")

    def test_03_bidirectional_communication(self):
        """Test comunicazione bidirezionale (ping-pong)."""
        self.logger.log("\n" + "=" * 80, Logger.INFO)
        self.logger.log("TEST 3: Bidirectional communication", Logger.INFO)
        self.logger.log("=" * 80, Logger.INFO)

        # Clear received commands
        self.received_commands.clear()

        # Send ping command
        ping_cmd = CommandEvent("ping", {"timestamp": time.time()})
        self.command_handler.send(ping_cmd.to_dict())

        self.logger.log("Sent ping command, waiting for pong response...", Logger.INFO)

        # Wait for response
        timeout = time.time() + 10
        response_received = False

        while time.time() < timeout:
            try:
                msg = self.received_commands.pop()
                if msg and msg.payload.get("command") == "pong":
                    self.logger.log(f"Received pong response: {msg}", Logger.INFO)
                    response_received = True
                    break
            except Exception as e:
                pass

            time.sleep(0.1)

        self.assertTrue(response_received, "Should receive pong response from client")

    def test_04_screen_switching(self):
        """Test cambio schermo attivo."""
        self.logger.log("\n" + "=" * 80, Logger.INFO)
        self.logger.log("TEST 4: Screen switching", Logger.INFO)
        self.logger.log("=" * 80, Logger.INFO)

        count_before = self._get_client_event_count()

        # Switch to inactive screen
        self.event_bus.dispatch(
            EventType.ACTIVE_SCREEN_CHANGED,
            {"active_screen": "right"}
        )
        time.sleep(0.5)

        # Send commands (should not arrive)
        for i in range(5):
            cmd = CommandEvent("test_inactive", {"index": i})
            self.command_handler.send(cmd.to_dict())
            time.sleep(0.05)

        time.sleep(1)

        count_inactive = self._get_client_event_count() - count_before
        self.logger.log(f"Received during inactive: {count_inactive} (expected: 0)", Logger.INFO)

        self.assertEqual(count_inactive, 0, "Should not receive events when screen is inactive")

        # Reactivate
        self.event_bus.dispatch(
            EventType.ACTIVE_SCREEN_CHANGED,
            {"active_screen": "center"}
        )
        time.sleep(0.5)

        count_before_reactivation = self._get_client_event_count()

        cmd = CommandEvent("reactivated", {})
        self.command_handler.send(cmd.to_dict())
        time.sleep(1)

        received_after_reactivation = self._get_client_event_count() - count_before_reactivation

        self.assertGreater(received_after_reactivation, 0, "Should receive events after reactivation")

    def test_05_stress_test(self):
        """Test stress con molti comandi rapidi."""
        self.logger.log("\n" + "=" * 80, Logger.INFO)
        self.logger.log("TEST 5: Stress test", Logger.INFO)
        self.logger.log("=" * 80, Logger.INFO)

        initial_count = self._get_client_event_count()
        event_count = 100

        start_time = time.time()
        for i in range(event_count):
            cmd = CommandEvent("stress", {"index": i, "data": "x" * 100})
            self.command_handler.send(cmd.to_dict())
            time.sleep(0.01)

        elapsed = time.time() - start_time
        actual_frequency = event_count / elapsed

        self.logger.log(f"Sent {event_count} commands at ~{actual_frequency:.1f} Hz", Logger.INFO)

        time.sleep(3)

        received_count = self._get_client_event_count() - initial_count
        reception_rate = (received_count / event_count) * 100
        self.logger.log(f"Received {received_count}/{event_count} commands ({reception_rate:.1f}%)", Logger.INFO)

        self.assertGreater(reception_rate, 40, "Should receive at least 40% under stress")


def suite():
    """Crea test suite."""
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestBidirectionalCommandStream))
    return suite


if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite())

    sys.exit(0 if result.wasSuccessful() else 1)
