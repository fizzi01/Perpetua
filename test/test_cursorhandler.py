import asyncio
import time

from event.EventBus import EventBus
from input.cursor import CursorHandlerWorker


class MockStreamHandler:
    """Mock stream handler per il benchmark"""
    def __init__(self):
        self.received_events = []
        self.receive_times = []
        self.total_events = 0
        self.start_time = None

    async def send(self, event):
        """Simula l'invio di un evento"""
        current_time = time.time()
        self.received_events.append(event)
        self.receive_times.append(current_time)
        self.total_events += 1

    def get_stats(self):
        """Calcola statistiche di performance"""
        if not self.receive_times or len(self.receive_times) < 2:
            return None

        elapsed = self.receive_times[-1] - self.receive_times[0]
        throughput = self.total_events / elapsed if elapsed > 0 else 0

        # Calcola latenze tra eventi consecutivi
        latencies = []
        for i in range(1, len(self.receive_times)):
            latency_ms = (self.receive_times[i] - self.receive_times[i-1]) * 1000
            latencies.append(latency_ms)

        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            min_latency = min(latencies)
            max_latency = max(latencies)
            # Calcola percentili
            sorted_latencies = sorted(latencies)
            p50 = sorted_latencies[len(sorted_latencies) // 2]
            p95 = sorted_latencies[int(len(sorted_latencies) * 0.95)]
            p99 = sorted_latencies[int(len(sorted_latencies) * 0.99)]
        else:
            avg_latency = min_latency = max_latency = p50 = p95 = p99 = 0

        return {
            'total_events': self.total_events,
            'elapsed_time': elapsed,
            'throughput': throughput,
            'avg_latency_ms': avg_latency,
            'min_latency_ms': min_latency,
            'max_latency_ms': max_latency,
            'p50_latency_ms': p50,
            'p95_latency_ms': p95,
            'p99_latency_ms': p99,
        }

    def reset(self):
        """Reset delle statistiche"""
        self.received_events = []
        self.receive_times = []
        self.total_events = 0


async def __main():
    print("=" * 80)
    print("BENCHMARK MOUSE LISTENER - Produzione/Ricezione Eventi")
    print("=" * 80)

    eb = EventBus()
    mock_stream = MockStreamHandler()
    controller = CursorHandlerWorker(eb, stream=mock_stream, debug=False)

    try:
        # Avvia la window
        controller.start()
        print("✓ Window avviata e pronta\n")

        # Test 1: Benchmark produzione eventi mouse
        print("-" * 80)
        print("TEST 1: Benchmark Cattura Mouse (muovi il mouse per 10 secondi)")
        print("-" * 80)

        result = await controller._on_active_screen_changed({"active_screen": "test_screen"})
        print(f"✓ Cattura mouse abilitata: {result}")
        print("\n>>> MUOVI IL MOUSE VELOCEMENTE PER 10 SECONDI <<<\n")

        # Monitora per 10 secondi
        benchmark_duration = 10
        start_benchmark = time.time()
        last_report = start_benchmark

        while time.time() - start_benchmark < benchmark_duration:
            await asyncio.sleep(1)
            current_time = time.time()

            # Report ogni secondo
            if current_time - last_report >= 1:
                elapsed = current_time - start_benchmark
                events_count = mock_stream.total_events
                rate = events_count / elapsed if elapsed > 0 else 0
                print(f"  [{elapsed:.1f}s] Eventi ricevuti: {events_count:5d} | Rate: {rate:6.1f} eventi/s")
                last_report = current_time

        # Disabilita cattura
        result = await controller._on_active_screen_changed({"active_screen": None})
        print(f"\n✓ Cattura mouse disabilitata: {result}\n")

        # Statistiche finali
        stats = mock_stream.get_stats()
        if stats:
            print("-" * 80)
            print("STATISTICHE FINALI:")
            print("-" * 80)
            print(f"  Eventi totali:           {stats['total_events']:,}")
            print(f"  Tempo trascorso:         {stats['elapsed_time']:.3f} secondi")
            print(f"  Throughput medio:        {stats['throughput']:.2f} eventi/secondo")
            print(f"\n  Latenza media:           {stats['avg_latency_ms']:.3f} ms")
            print(f"  Latenza minima:          {stats['min_latency_ms']:.3f} ms")
            print(f"  Latenza massima:         {stats['max_latency_ms']:.3f} ms")
            print(f"\n  Percentili latenza:")
            print(f"    P50 (mediana):         {stats['p50_latency_ms']:.3f} ms")
            print(f"    P95:                   {stats['p95_latency_ms']:.3f} ms")
            print(f"    P99:                   {stats['p99_latency_ms']:.3f} ms")

            # Valutazione performance
            print(f"\n  VALUTAZIONE:")
            if stats['throughput'] > 100:
                print(f"    ✓ Throughput ECCELLENTE (>{100} eventi/s)")
            elif stats['throughput'] > 50:
                print(f"    ✓ Throughput BUONO (>{50} eventi/s)")
            else:
                print(f"    ⚠ Throughput BASSO (<{50} eventi/s)")

            if stats['p95_latency_ms'] < 10:
                print(f"    ✓ Latenza ECCELLENTE (P95 <10ms)")
            elif stats['p95_latency_ms'] < 20:
                print(f"    ✓ Latenza BUONA (P95 <20ms)")
            else:
                print(f"    ⚠ Latenza ALTA (P95 >{20}ms)")
        else:
            print("⚠ Nessun evento ricevuto durante il benchmark")

        print("-" * 80)

        # Test 2: Test stress con enable/disable rapidi
        print("\nTEST 2: Stress Test - Enable/Disable rapidi")
        print("-" * 80)

        mock_stream.reset()
        cycles = 1

        for i in range(cycles):
            print(f"  Ciclo {i+1}/{cycles}: Enable -> Wait -> Disable")
            await controller._on_active_screen_changed({"active_screen": "test_screen"})
            await asyncio.sleep(2)
            await controller._on_active_screen_changed({"active_screen": None})
            await asyncio.sleep(0.5)

        print(f"✓ Completati {cycles} cicli di enable/disable\n")

    except Exception as e:
        print(f"\n✗ Errore durante il benchmark: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Ferma la window
        print("-" * 80)
        print("Chiusura...")
        await controller.stop()
        print("✓ Benchmark completato!")
        print("=" * 80)

if __name__ == "__main__":
    asyncio.run(__main())
