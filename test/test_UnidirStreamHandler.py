import json
import unittest
import time
from pathlib import Path

from network.stream.ServerCustomStream import UnidirectionalStreamHandler
from network.connection.ServerConnectionServices import ServerConnectionHandler
from network.stream.StreamObj import StreamType
from model.ClientObj import ClientsManager, ClientObj
from event.EventBus import ThreadSafeEventBus
from event.Event import EventType, MouseEvent
from utils.logging.logger import Logger

import subprocess


class TestMouseStreamHandler(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """Setup iniziale per tutti i test."""
        cls.logger = Logger(stdout=print, logging=True)
        cls.logger.set_level(Logger.DEBUG)
        cls.host = "127.0.0.1"
        cls.base_port = 5051
        # Create temporary file for client events in temp folder with temp lib
        cls.events_file = Path("test/temp/client_mouse_events.json")
        # Ensure the temp directory exists
        cls.events_file.parent.mkdir(parents=True, exist_ok=True)

        cls.event_bus = ThreadSafeEventBus()
        cls.clients_manager = ClientsManager()

        cls._setup_server()
        cls._create_client_process()
        cls._wait_for_external_client()
        cls._setup_mouse_handler()

    @classmethod
    def _create_client_process(cls):
        """Crea il processo del client esterno."""
        cls.client_process = subprocess.Popen([
            "python3", "standalone_test_client.py",
            "--host", cls.host,
            "--port", str(cls.base_port),
            "--position", "center",
            "--events-file", str(cls.events_file)
        ])

        # Attendi un po' per assicurarti che il processo sia avviato
        time.sleep(2)


    @classmethod
    def _wait_for_external_client(cls):
        """Attende che il client esterno si connetta."""
        cls.logger.log("", Logger.INFO)
        cls.logger.log("=" * 80, Logger.INFO)
        cls.logger.log("AVVIA IL CLIENT ESTERNO IN UN ALTRO TERMINALE:", Logger.INFO)
        cls.logger.log("", Logger.INFO)
        cls.logger.log(f"  python3 test/standalone_test_client.py \\", Logger.INFO)
        cls.logger.log(f"    --host {cls.host} \\", Logger.INFO)
        cls.logger.log(f"    --port {cls.base_port} \\", Logger.INFO)
        cls.logger.log(f"    --position center \\", Logger.INFO)
        cls.logger.log(f"    --events-file {cls.events_file}", Logger.INFO)
        cls.logger.log("", Logger.INFO)
        cls.logger.log("=" * 80, Logger.INFO)
        cls.logger.log("", Logger.INFO)

        timeout = time.time() + 60
        check_interval = 2

        while time.time() < timeout:
            status = cls._read_client_status()

            if status.get("status") == "receiving":
                cls.logger.log("Client esterno connesso e pronto!", Logger.INFO)
                time.sleep(1)
                return

            if status.get("status") == "error":
                cls.fail(f"Client error: {status.get('error')}")

            remaining = int(timeout - time.time())
            cls.logger.log(
                f"In attesa del client... (timeout: {remaining}s, status: {status.get('status', 'unknown')})",
                Logger.DEBUG
            )
            time.sleep(check_interval)

        cls.fail("Client esterno non si è connesso entro il timeout")

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

    def _get_client_event_count(self):
        """Ottiene il numero di eventi ricevuti dal client."""
        status = self._read_client_status()
        return status.get("count", 0)

    @classmethod
    def _setup_server(cls):
        """Inizializza il server."""
        # Configura client whitelisted
        test_client = ClientObj(
            ip_address=cls.host,
            screen_position="center",
            client_name="TestClient",
            ssl=False
        )
        cls.clients_manager.add_client(test_client)

        # Crea server
        cls.server = ServerConnectionHandler(
            host=cls.host,
            port=cls.base_port,
            wait=5,
            heartbeat_interval=30,
            whitelist=cls.clients_manager,
            connected_callback=cls._on_client_connected,
            disconnected_callback=cls._on_client_disconnected
        )

        cls.server.initialize()

        success = cls.server.start()
        cls.assertTrue(success, "Server should start successfully")

        cls.logger.log(f"Server started on {cls.host}:{cls.base_port}", Logger.INFO)

    @classmethod
    def _setup_mouse_handler(cls):
        """Inizializza MouseStreamHandler."""
        cls.mouse_handler = UnidirectionalStreamHandler(
            stream_type=StreamType.MOUSE,
            clients=cls.clients_manager,
            event_bus=cls.event_bus
        )
        cls.mouse_handler.start()

        # Attiva client per ricezione
        cls.event_bus.dispatch(
            EventType.ACTIVE_SCREEN_CHANGED,
            {"active_screen": "center"}
        )
        time.sleep(0.5)

    @classmethod
    def _on_client_connected(cls, client: ClientObj):
        """Callback connessione client."""
        cls.logger.log(f"Server: Client {client.client_name} connected callback", Logger.INFO)

    @classmethod
    def _on_client_disconnected(cls, client: ClientObj):
        """Callback disconnessione client."""
        cls.logger.log(f"Server: Client {client.client_name} disconnected callback", Logger.WARNING)

    
    def test_basic_mouse_move_events(self):
        """Test invio base di eventi mouse move."""
        test_positions = [
            (100, 200),
            (150, 250),
            (200, 300),
        ]

        initial_count = self._get_client_event_count()

        for x, y in test_positions:
            mouse_event = MouseEvent(x=x, y=y, action="move")
            self.mouse_handler.send(mouse_event.to_dict())
            time.sleep(0.1)

        # Attendi ricezione
        time.sleep(2)
        
        received_count = self._get_client_event_count() - initial_count
        self.logger.log(f"Test: Received {received_count} mouse move events", Logger.INFO)

        self.assertGreater(received_count, 0, "Client should receive mouse events")

    def test_high_frequency_mouse_events(self):
        """Test eventi mouse ad alta frequenza."""
        event_count = 50
        interval = 0.02  # 50ms = ~20Hz
        initial_count = self._get_client_event_count()
        for i in range(event_count):
            x = 100 + i
            y = 200 + (i % 50)
            mouse_event = MouseEvent(x=x, y=y, action="move")
            self.mouse_handler.send(mouse_event.to_dict())
            time.sleep(interval)

        time.sleep(2)

        received_count = self._get_client_event_count() - initial_count
        reception_rate = (received_count / event_count) * 100

        self.logger.log(
            f"Test: Received {received_count}/{event_count} events ({reception_rate:.1f}%)",
            Logger.INFO
        )

        self.assertGreater(
            reception_rate, 50,
            f"Should receive at least 50% of events (got {reception_rate:.1f}%)"
        )


    def test_very_high_frequency_stress(self):
        """Test stress con frequenza molto alta (~200 eventi/sec)."""
        event_count = 200
        interval = 0.005  # 5ms = ~200Hz

        start_time = time.time()
        initial_count = self._get_client_event_count()
        for i in range(event_count):
            x = 500 + (i % 100)
            y = 300 + (i % 80)
            mouse_event = MouseEvent(x=x, y=y, action="move")
            self.mouse_handler.send(mouse_event.to_dict())
            time.sleep(interval)

        elapsed = time.time() - start_time
        actual_frequency = event_count / elapsed

        self.logger.log(
            f"Test: Stress test - Sent {event_count} events at ~{actual_frequency:.1f} Hz",
            Logger.INFO
        )

        time.sleep(2)

        received_count = self._get_client_event_count() - initial_count
        reception_rate = (received_count / event_count) * 100

        self.logger.log(
            f"Test: Stress test - Received {received_count}/{event_count} events ({reception_rate:.1f}%)",
            Logger.INFO
        )

        # A frequenza molto alta, accettiamo anche solo 50% di ricezione
        self.assertGreater(
            reception_rate, 50,
            f"Should receive at least 50% of very high-frequency events (got {reception_rate:.1f}%)"
        )

    def test_active_screen_switching(self):
        """Test cambio schermo attivo."""
        initial_count = self._get_client_event_count()
        # Aggiungi secondo client
        second_client = ClientObj(
            ip_address="127.0.0.2",
            screen_position="right",
            client_name="SecondClient",
            ssl=False
        )
        self.clients_manager.add_client(second_client)

        # Invia evento su primo client
        mouse_event = MouseEvent(x=100, y=100, action="move")
        self.mouse_handler.send(mouse_event.to_dict())
        time.sleep(0.3)

        initial_count = self._get_client_event_count() - initial_count
        self.assertGreater(initial_count, 0, "Should receive event when active")
    
        count_before_switch = self._get_client_event_count()

        # Cambia a schermo "right" (non connesso)
        self.event_bus.dispatch(
            EventType.ACTIVE_SCREEN_CHANGED,
            {"active_screen": "right"}
        )
        time.sleep(0.5)

        # Invia eventi (non dovrebbero arrivare)
        for i in range(5):
            mouse_event = MouseEvent(x=200 + i, y=200 + i, action="move")
            self.mouse_handler.send(mouse_event.to_dict())
            time.sleep(0.05)

        time.sleep(0.5)

        received_during_inactive = self._get_client_event_count() - count_before_switch
        self.assertEqual(
            received_during_inactive, 0,
            "Should not receive events when screen is inactive"
        )

        # Riattiva primo client
        self.event_bus.dispatch(
            EventType.ACTIVE_SCREEN_CHANGED,
            {"active_screen": "center"}
        )
        time.sleep(0.5)

        # Invia nuovo evento
        mouse_event = MouseEvent(x=300, y=300, action="move")
        self.mouse_handler.send(mouse_event.to_dict())
        time.sleep(0.5)

        received_after_reactivation = self._get_client_event_count() - initial_count
        self.assertGreater(
            received_after_reactivation, 0,
            "Should receive events again after reactivating screen"
        )

    def test_mouse_click_events(self):
        """Test eventi click mouse."""
        initial_count = self._get_client_event_count()

        click_event = MouseEvent(x=150, y=150, button=1, action="click", is_presed=True)
        self.mouse_handler.send(click_event.to_dict())
        time.sleep(0.1)

        release_event = MouseEvent(x=150, y=150, button=1, action="release", is_presed=False)
        self.mouse_handler.send(release_event.to_dict())
        time.sleep(1)

        received_count = self._get_client_event_count() - initial_count

        self.logger.log(f"Test: Received {received_count} click/release events", Logger.INFO)

        self.assertGreaterEqual(
            received_count, 2,
            "Should receive both click and release events"
        )

if __name__ == '__main__':

    # Esegui test con verbosità
    unittest.main(verbosity=2)
