#!/usr/bin/env python3
"""
Test manuale standalone per MouseStreamHandler - senza unittest.
Esegue test in sequenza con client esterno.
"""
import json
import os
import sys
import time
from pathlib import Path

# Aggiungi il path del progetto
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from network.stream.CustomStream import UnidirectionalStreamHandler
from network.connection.ServerConnectionServices import ServerConnectionHandler
from network.stream.StreamObj import StreamType
from model.ClientObj import ClientsManager, ClientObj
from event.EventBus import ThreadSafeEventBus
from event.Event import EventType, MouseEvent
from utils.logging.logger import Logger


class ManualTestRunner:
    """Runner per test manuali senza unittest."""

    def __init__(self):
        self.logger = Logger(stdout=print, logging=True)
        self.logger.set_level(Logger.DEBUG)
        self.host = "127.0.0.1"
        self.base_port = 5051
        self.events_file = Path("test/temp/client_mouse_events.json")
        self.events_file.parent.mkdir(parents=True, exist_ok=True)

        self.event_bus = None
        self.clients_manager = None
        self.server = None
        self.mouse_handler = None

        self.tests_passed = 0
        self.tests_failed = 0

    def setup(self):
        """Setup iniziale."""
        self.logger.log("=" * 80, Logger.INFO)
        self.logger.log("SETUP TEST ENVIRONMENT", Logger.INFO)
        self.logger.log("=" * 80, Logger.INFO)

        self.event_bus = ThreadSafeEventBus()
        self.clients_manager = ClientsManager()

        self._setup_server()
        self._wait_for_external_client()
        self._setup_mouse_handler()

        self.logger.log("\n✓ Setup completato\n", Logger.INFO)

    def teardown(self):
        """Cleanup."""
        self.logger.log("\n" + "=" * 80, Logger.INFO)
        self.logger.log("CLEANUP", Logger.INFO)
        self.logger.log("=" * 80, Logger.INFO)

        if self.mouse_handler:
            self.mouse_handler.stop()

        time.sleep(1)

        if self.server:
            self.server.stop()

        time.sleep(1)
        self.logger.log("✓ Cleanup completato\n", Logger.INFO)

    def _setup_server(self):
        """Inizializza il server."""
        test_client = ClientObj(
            ip_address=self.host,
            screen_position="center",
            client_name="TestClient",
            ssl=False
        )
        self.clients_manager.add_client(test_client)

        self.server = ServerConnectionHandler(
            host=self.host,
            port=self.base_port,
            wait=5,
            heartbeat_interval=30,
            whitelist=self.clients_manager,
            connected_callback=self._on_client_connected,
            disconnected_callback=self._on_client_disconnected
        )

        self.server.initialize()
        success = self.server.start()

        if not success:
            self.logger.log("✗ Server failed to start", Logger.ERROR)
            sys.exit(1)

        self.logger.log(f"✓ Server started on {self.host}:{self.base_port}", Logger.INFO)

    def _setup_mouse_handler(self):
        """Inizializza MouseStreamHandler."""
        self.mouse_handler = UnidirectionalStreamHandler(
            stream_type=StreamType.MOUSE,
            clients=self.clients_manager,
            event_bus=self.event_bus
        )
        self.mouse_handler.start()

        self.event_bus.dispatch(
            EventType.ACTIVE_SCREEN_CHANGED,
            {"active_screen": "center"}
        )
        time.sleep(0.5)
        self.logger.log("✓ Mouse handler initialized", Logger.INFO)

    def _wait_for_external_client(self):
        """Attende che il client esterno si connetta."""
        self.logger.log("\n" + "=" * 80, Logger.INFO)
        self.logger.log("AVVIA IL CLIENT ESTERNO IN UN ALTRO TERMINALE:", Logger.INFO)
        self.logger.log("", Logger.INFO)
        self.logger.log(f"  python3 test/standalone_test_client.py \\", Logger.INFO)
        self.logger.log(f"    --host {self.host} \\", Logger.INFO)
        self.logger.log(f"    --port {self.base_port} \\", Logger.INFO)
        self.logger.log(f"    --position center \\", Logger.INFO)
        self.logger.log(f"    --events-file {self.events_file}", Logger.INFO)
        self.logger.log("", Logger.INFO)
        self.logger.log("=" * 80 + "\n", Logger.INFO)

        timeout = time.time() + 360
        check_interval = 2

        while time.time() < timeout:
            status = self._read_client_status()

            if status.get("status") == "receiving":
                self.logger.log("✓ Client esterno connesso e pronto!\n", Logger.INFO)
                time.sleep(1)
                return

            if status.get("status") == "error":
                self.logger.log(f"✗ Client error: {status.get('error')}", Logger.ERROR)
                sys.exit(1)

            remaining = int(timeout - time.time())
            print(f"\rIn attesa del client... (timeout: {remaining}s, status: {status.get('status', 'unknown')})", end="", flush=True)
            time.sleep(check_interval)

        self.logger.log("\n✗ Client esterno non si è connesso entro il timeout", Logger.ERROR)
        sys.exit(1)

    def _read_client_status(self):
        """Legge lo stato corrente dal file eventi."""
        try:
            if not self.events_file.exists():
                return {}
            with open(self.events_file, 'r') as f:
                return json.load(f)
        except:
            return {}

    def _get_client_event_count(self):
        """Ottiene il numero di eventi ricevuti dal client."""
        status = self._read_client_status()
        return status.get("count", 0)

    def _on_client_connected(self, client: ClientObj):
        """Callback connessione client."""
        self.logger.log(f"Server: Client {client.client_name} connected", Logger.INFO)

    def _on_client_disconnected(self, client: ClientObj):
        """Callback disconnessione client."""
        self.logger.log(f"Server: Client {client.client_name} disconnected", Logger.WARNING)

    def assert_greater(self, actual, expected, message):
        """Asserzione personalizzata."""
        if actual > expected:
            self.logger.log(f"  ✓ {message} ({actual} > {expected})", Logger.INFO)
            return True
        else:
            self.logger.log(f"  ✗ FAIL: {message} ({actual} <= {expected})", Logger.ERROR)
            return False

    def assert_equal(self, actual, expected, message):
        """Asserzione personalizzata."""
        if actual == expected:
            self.logger.log(f"  ✓ {message} ({actual} == {expected})", Logger.INFO)
            return True
        else:
            self.logger.log(f"  ✗ FAIL: {message} ({actual} != {expected})", Logger.ERROR)
            return False

    def assert_greater_equal(self, actual, expected, message):
        """Asserzione personalizzata."""
        if actual >= expected:
            self.logger.log(f"  ✓ {message} ({actual} >= {expected})", Logger.INFO)
            return True
        else:
            self.logger.log(f"  ✗ FAIL: {message} ({actual} < {expected})", Logger.ERROR)
            return False

    def run_test(self, test_name, test_func):
        """Esegue un singolo test."""
        self.logger.log("\n" + "=" * 80, Logger.INFO)
        self.logger.log(f"TEST: {test_name}", Logger.INFO)
        self.logger.log("=" * 80, Logger.INFO)

        try:
            result = test_func()
            if result:
                self.tests_passed += 1
                self.logger.log(f"\n✓ {test_name} PASSED\n", Logger.INFO)
            else:
                self.tests_failed += 1
                self.logger.log(f"\n✗ {test_name} FAILED\n", Logger.ERROR)
        except Exception as e:
            self.tests_failed += 1
            self.logger.log(f"\n✗ {test_name} ERROR: {e}\n", Logger.ERROR)
            import traceback
            traceback.print_exc()

    # ==================== TEST CASES ====================

    def test_basic_mouse_move_events(self):
        """Test invio base di eventi mouse move."""
        test_positions = [(100, 200), (150, 250), (200, 300)]
        initial_count = self._get_client_event_count()

        for x, y in test_positions:
            mouse_event = MouseEvent(x=x, y=y, action="move")
            self.mouse_handler.send(mouse_event.to_dict())
            time.sleep(0.1)

        time.sleep(2)

        received_count = self._get_client_event_count() - initial_count
        self.logger.log(f"Ricevuti {received_count} eventi su {len(test_positions)} inviati", Logger.INFO)

        return self.assert_greater(received_count, 0, "Client should receive mouse events")

    def test_high_frequency_mouse_events(self):
        """Test eventi mouse ad alta frequenza."""
        event_count = 50
        interval = 0.02
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

        self.logger.log(f"Ricevuti {received_count}/{event_count} eventi ({reception_rate:.1f}%)", Logger.INFO)

        return self.assert_greater(reception_rate, 50, "Should receive at least 50% of events")

    def test_very_high_frequency_stress(self):
        """Test stress con frequenza molto alta (~200 eventi/sec)."""
        event_count = 200
        interval = 0.005

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

        self.logger.log(f"Inviati {event_count} eventi a ~{actual_frequency:.1f} Hz", Logger.INFO)

        time.sleep(2)

        received_count = self._get_client_event_count() - initial_count
        reception_rate = (received_count / event_count) * 100

        self.logger.log(f"Ricevuti {received_count}/{event_count} eventi ({reception_rate:.1f}%)", Logger.INFO)

        return self.assert_greater(reception_rate, 50, "Should receive at least 50% of high-frequency events")

    def test_active_screen_switching(self):
        """Test cambio schermo attivo."""
        initial_count = self._get_client_event_count()

        second_client = ClientObj(
            ip_address="127.0.0.2",
            screen_position="right",
            client_name="SecondClient",
            ssl=False
        )
        self.clients_manager.add_client(second_client)

        mouse_event = MouseEvent(x=100, y=100, action="move")
        self.mouse_handler.send(mouse_event.to_dict())
        time.sleep(0.5)

        count_while_active = self._get_client_event_count() - initial_count
        if not self.assert_greater(count_while_active, 0, "Should receive event when active"):
            return False

        count_before_switch = self._get_client_event_count()

        self.event_bus.dispatch(EventType.ACTIVE_SCREEN_CHANGED, {"active_screen": "right"})
        time.sleep(0.5)

        for i in range(5):
            mouse_event = MouseEvent(x=200 + i, y=200 + i, action="move")
            self.mouse_handler.send(mouse_event.to_dict())
            time.sleep(0.05)

        time.sleep(1)

        received_during_inactive = self._get_client_event_count() - count_before_switch
        if not self.assert_equal(received_during_inactive, 0, "Should not receive events when inactive"):
            return False

        self.event_bus.dispatch(EventType.ACTIVE_SCREEN_CHANGED, {"active_screen": "center"})
        time.sleep(0.5)

        count_before_reactivation = self._get_client_event_count()

        mouse_event = MouseEvent(x=300, y=300, action="move")
        self.mouse_handler.send(mouse_event.to_dict())
        time.sleep(1)

        received_after_reactivation = self._get_client_event_count() - count_before_reactivation
        return self.assert_greater(received_after_reactivation, 0, "Should receive events after reactivation")

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

        self.logger.log(f"Ricevuti {received_count} eventi click/release", Logger.INFO)

        return self.assert_greater_equal(received_count, 2, "Should receive both click and release events")

    def run_all_tests(self):
        """Esegue tutti i test."""
        self.logger.log("\n" + "=" * 80, Logger.INFO)
        self.logger.log("ESECUZIONE TEST SUITE", Logger.INFO)
        self.logger.log("=" * 80 + "\n", Logger.INFO)

        self.run_test("test_basic_mouse_move_events", self.test_basic_mouse_move_events)
        self.run_test("test_high_frequency_mouse_events", self.test_high_frequency_mouse_events)
        self.run_test("test_very_high_frequency_stress", self.test_very_high_frequency_stress)
        self.run_test("test_active_screen_switching", self.test_active_screen_switching)
        self.run_test("test_mouse_click_events", self.test_mouse_click_events)

        self.print_summary()

    def print_summary(self):
        """Stampa riepilogo risultati."""
        total = self.tests_passed + self.tests_failed

        self.logger.log("\n" + "=" * 80, Logger.INFO)
        self.logger.log("RIEPILOGO TEST", Logger.INFO)
        self.logger.log("=" * 80, Logger.INFO)
        self.logger.log(f"Totale test eseguiti: {total}", Logger.INFO)
        self.logger.log(f"✓ Passati: {self.tests_passed}", Logger.INFO)
        self.logger.log(f"✗ Falliti: {self.tests_failed}", Logger.ERROR if self.tests_failed > 0 else Logger.INFO)
        self.logger.log("=" * 80 + "\n", Logger.INFO)

        if self.tests_failed == 0:
            self.logger.log("🎉 TUTTI I TEST PASSATI!", Logger.INFO)
            return 0
        else:
            self.logger.log("❌ ALCUNI TEST FALLITI", Logger.ERROR)
            return 1


def main():
    """Entry point."""
    runner = ManualTestRunner()

    try:
        runner.setup()
        runner.run_all_tests()
        runner.teardown()
        sys.exit(0)

    except KeyboardInterrupt:
        print("\n\nTest interrotti dall'utente")
        runner.teardown()
        sys.exit(130)

    except Exception as e:
        print(f"\n\nErrore fatale: {e}")
        import traceback
        traceback.print_exc()
        runner.teardown()
        sys.exit(1)


if __name__ == '__main__':
    print("\n" + "=" * 80)
    print("TEST MANUALE MOUSE STREAM HANDLER")
    print("=" * 80)
    print("\nQuesto script eseguirà una serie di test sul MouseStreamHandler.")
    print("È necessario avviare il client in un terminale separato quando richiesto.\n")
    print("=" * 80 + "\n")

    main()
