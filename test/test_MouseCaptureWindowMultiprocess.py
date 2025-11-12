import unittest
import time
from test_warp_multiprocess import MouseCaptureWindowController


class TestMouseCaptureWindowMultiprocess(unittest.TestCase):
    """Test suite per la window di cattura mouse con multiprocessing"""

    def setUp(self):
        """Setup eseguito prima di ogni test"""
        self.controller = MouseCaptureWindowController()
        self.controller.start(wait_ready=True, timeout=10)
        time.sleep(0.5)  # Stabilizzazione

    def tearDown(self):
        """Cleanup eseguito dopo ogni test"""
        try:
            self.controller.stop(timeout=3)
        except:
            pass

    def test_window_startup(self):
        """Test che la window si avvii correttamente"""
        self.assertTrue(self.controller.is_running)

        # Verifica che la window risponda
        result = self.controller.set_message("Test message")
        self.assertIsNotNone(result)
        self.assertTrue(result.get('success', False))

    def test_enable_disable_capture(self):
        """Test abilitazione e disabilitazione cattura"""
        # Abilita cattura
        result = self.controller.enable_capture()
        self.assertIsNotNone(result)
        self.assertEqual(result.get('type'), 'capture_enabled')
        self.assertTrue(result.get('success', False))

        time.sleep(0.2)

        # Verifica stato
        stats = self.controller.get_stats()
        self.assertTrue(stats.get('is_captured', False))

        # Disabilita cattura
        result = self.controller.disable_capture()
        self.assertIsNotNone(result)
        self.assertEqual(result.get('type'), 'capture_disabled')
        self.assertTrue(result.get('success', False))

        time.sleep(0.2)

        # Verifica stato
        stats = self.controller.get_stats()
        self.assertFalse(stats.get('is_captured', True))

    def test_recording_workflow(self):
        """Test del workflow completo di registrazione"""
        # Abilita cattura
        self.controller.enable_capture()
        time.sleep(0.3)

        # Inizia registrazione
        result = self.controller.start_recording()
        self.assertEqual(result.get('type'), 'recording_started')
        self.assertTrue(result.get('success', False))

        # Verifica che la registrazione sia attiva
        stats = self.controller.get_stats()
        self.assertTrue(stats.get('is_recording', False))

        # Simula movimento del mouse (l'utente dovrebbe muovere il mouse)
        print("\n=== Muovi il mouse nella window per 3 secondi ===")
        time.sleep(3)

        # Ferma registrazione
        result = self.controller.stop_recording()
        self.assertEqual(result.get('type'), 'recording_stopped')
        self.assertTrue(result.get('success', False))

        # Verifica che ci siano movimenti registrati
        movements = result.get('movements', [])
        print(f"\nMovimenti registrati: {len(movements)}")

        # Verifica che la registrazione sia fermata
        stats = self.controller.get_stats()
        self.assertFalse(stats.get('is_recording', True))
        self.assertEqual(stats.get('movement_count'), len(movements))

    def test_clear_recording(self):
        """Test cancellazione registrazione"""
        # Abilita cattura e registra
        self.controller.enable_capture()
        time.sleep(0.3)
        self.controller.start_recording()
        time.sleep(1)  # Aspetta un po'
        result = self.controller.stop_recording()

        movements_count = len(result.get('movements', []))
        if movements_count > 0:
            print(f"\nMovimenti prima della cancellazione: {movements_count}")

        # Cancella
        result = self.controller.clear_recording()
        self.assertEqual(result.get('type'), 'recording_cleared')
        self.assertTrue(result.get('success', False))

        # Verifica che sia stato cancellato
        stats = self.controller.get_stats()
        self.assertEqual(stats.get('movement_count'), 0)

    def test_mouse_events_collection(self):
        """Test raccolta eventi mouse in tempo reale"""
        # Abilita cattura
        self.controller.enable_capture()
        time.sleep(0.3)

        print("\n=== Muovi il mouse nella window per 2 secondi ===")

        # Raccoglie eventi per 2 secondi
        events = self.controller.collect_mouse_events(duration=2.0)

        print(f"\nEventi raccolti: {len(events)}")

        if len(events) > 0:
            # Verifica struttura eventi
            first_event = events[0]
            self.assertEqual(first_event.get('type'), 'mouse_move')
            self.assertIn('dx', first_event)
            self.assertIn('dy', first_event)
            self.assertIn('total_dx', first_event)
            self.assertIn('total_dy', first_event)

            # Calcola statistiche
            total_dx = sum(e.get('dx', 0) for e in events)
            total_dy = sum(e.get('dy', 0) for e in events)
            print(f"Delta totale: dx={total_dx}, dy={total_dy}")

            # Verifica che l'ultimo evento abbia i totali corretti
            last_event = events[-1]
            self.assertEqual(last_event.get('total_dx'), total_dx)
            self.assertEqual(last_event.get('total_dy'), total_dy)

    def test_stats_retrieval(self):
        """Test recupero statistiche"""
        # Ottieni statistiche iniziali
        stats = self.controller.get_stats()

        self.assertIsNotNone(stats)
        self.assertEqual(stats.get('type'), 'stats')
        self.assertIn('total_dx', stats)
        self.assertIn('total_dy', stats)
        self.assertIn('is_captured', stats)
        self.assertIn('is_recording', stats)
        self.assertIn('movement_count', stats)

        # Verifica valori iniziali
        self.assertEqual(stats.get('total_dx'), 0)
        self.assertEqual(stats.get('total_dy'), 0)
        self.assertFalse(stats.get('is_captured'))
        self.assertFalse(stats.get('is_recording'))
        self.assertEqual(stats.get('movement_count'), 0)

    def test_message_setting(self):
        """Test impostazione messaggi"""
        messages = [
            "Test message 1",
            "Test message 2",
            "Test with special chars: àèéìòù!"
        ]

        for msg in messages:
            result = self.controller.set_message(msg)
            self.assertIsNotNone(result)
            self.assertEqual(result.get('type'), 'message_set')
            self.assertTrue(result.get('success', False))
            time.sleep(0.1)

    def test_high_frequency_events(self):
        """Test gestione eventi ad alta frequenza"""
        # Abilita cattura
        self.controller.enable_capture()
        time.sleep(0.3)

        print("\n=== Muovi il mouse VELOCEMENTE nella window per 3 secondi ===")

        # Raccoglie eventi per 3 secondi
        start_time = time.time()
        events = self.controller.collect_mouse_events(duration=3.0)
        duration = time.time() - start_time

        print(f"\nEventi raccolti: {len(events)}")
        print(f"Durata: {duration:.2f}s")

        if len(events) > 0:
            # Calcola frequenza
            frequency = len(events) / duration
            print(f"Frequenza media: {frequency:.2f} eventi/sec")

            # Verifica che ci siano abbastanza eventi (almeno 10/sec se l'utente ha mosso il mouse)
            # Nota: questo test dipende dall'interazione dell'utente
            if len(events) >= 10:
                self.assertGreater(frequency, 1.0)

                # Calcola movimento totale
                total_movement = sum(abs(e.get('dx', 0)) + abs(e.get('dy', 0)) for e in events)
                avg_movement = total_movement / len(events)
                print(f"Movimento medio per evento: {avg_movement:.2f} pixel")


class TestMouseCaptureWindowStressTest(unittest.TestCase):
    """Test di stress per il sistema multiprocesso"""

    def test_multiple_start_stop(self):
        """Test avvio e arresto multipli"""
        for i in range(3):
            print(f"\n=== Ciclo {i+1}/3 ===")
            controller = MouseCaptureWindowController()

            # Avvia
            controller.start(wait_ready=True, timeout=10)
            self.assertTrue(controller.is_running)

            # Usa brevemente
            time.sleep(0.5)
            result = controller.set_message(f"Test cycle {i+1}")
            self.assertIsNotNone(result)

            # Ferma
            controller.stop(timeout=3)
            self.assertFalse(controller.is_running)

            time.sleep(0.5)

    def test_rapid_commands(self):
        """Test invio rapido di comandi"""
        controller = MouseCaptureWindowController()
        controller.start(wait_ready=True, timeout=10)

        try:
            # Invia molti comandi rapidamente
            for i in range(20):
                controller.set_message(f"Message {i}")
                time.sleep(0.05)

            # Verifica che la window risponda ancora
            stats = controller.get_stats()
            self.assertIsNotNone(stats)

        finally:
            controller.stop(timeout=3)


def run_interactive_test():
    """Test interattivo per testing manuale"""
    print("=" * 60)
    print("TEST INTERATTIVO - Mouse Capture Window Multiprocess")
    print("=" * 60)

    controller = MouseCaptureWindowController()

    try:
        print("\n1. Avvio window...")
        controller.start(wait_ready=True)
        print("   ✓ Window avviata")

        time.sleep(1)

        print("\n2. Abilitazione cattura mouse...")
        controller.set_message("Cattura mouse tra 2 secondi...")
        time.sleep(2)
        result = controller.enable_capture()
        print(f"   ✓ Cattura abilitata: {result}")

        time.sleep(1)

        print("\n3. Inizio registrazione...")
        controller.start_recording()
        controller.set_message("REGISTRAZIONE - Muovi il mouse!")
        print("   ✓ Registrazione avviata")
        print("\n   >>> MUOVI IL MOUSE NELLA WINDOW PER 5 SECONDI <<<")

        events = controller.collect_mouse_events(duration=5.0)
        print(f"\n   ✓ Eventi raccolti: {len(events)}")

        print("\n4. Arresto registrazione...")
        result = controller.stop_recording()
        movements = result.get('movements', [])
        print(f"   ✓ Movimenti registrati: {len(movements)}")

        print("\n5. Recupero statistiche...")
        stats = controller.get_stats()
        print(f"   ✓ Statistiche:")
        print(f"      - Total DX: {stats.get('total_dx')}")
        print(f"      - Total DY: {stats.get('total_dy')}")
        print(f"      - Movement count: {stats.get('movement_count')}")

        print("\n6. Chiusura tra 2 secondi...")
        controller.set_message("Chiusura tra 2 secondi...")
        time.sleep(2)

    finally:
        print("\n7. Arresto window...")
        controller.stop()
        print("   ✓ Window arrestata")
        print("\n" + "=" * 60)
        print("TEST COMPLETATO")
        print("=" * 60)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == '--interactive':
        run_interactive_test()
    else:
        unittest.main()

