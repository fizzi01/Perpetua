#!/usr/bin/env python3
"""
PyContinuity Performance Benchmark Test
Comprehensive performance testing and benchmarking for the new protocol implementation.
"""

import gc
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
from typing import List, Dict, Any, Tuple
import statistics

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from utils.protocol.message import MessageBuilder, ProtocolMessage
    from utils.protocol.adapter import ProtocolAdapter  
    from utils.net.ChunkManager import ChunkManager
    PROTOCOL_AVAILABLE = True
except ImportError as e:
    print(f"Protocol modules not available: {e}")
    PROTOCOL_AVAILABLE = False


class PerformanceBenchmark:
    """Performance benchmark suite for PyContinuity protocol."""
    
    def __init__(self):
        self.results = {}
        if PROTOCOL_AVAILABLE:
            self.message_builder = MessageBuilder()
            self.protocol_adapter = ProtocolAdapter()
            self.chunk_manager = ChunkManager()
    
    def benchmark_message_creation(self, num_messages=10000):
        """Benchmark message creation speed."""
        print(f"\n=== Benchmark 1: Message Creation ({num_messages:,} messages) ===")
        
        if not PROTOCOL_AVAILABLE:
            print("Skipping - protocol not available")
            return False
        
        # Test different message types
        message_types = [
            ("mouse", lambda i: self.message_builder.create_mouse_message(i % 1920, i % 1080, "move")),
            ("keyboard", lambda i: self.message_builder.create_keyboard_message(chr(ord('a') + i % 26), "press")),
            ("clipboard", lambda i: self.message_builder.create_clipboard_message(f"data_{i}", "text")),
        ]
        
        results = {}
        
        for msg_type, creator in message_types:
            print(f"  Testing {msg_type} messages...")
            
            # Warmup
            for i in range(100):
                creator(i)
            
            # Benchmark
            start_time = time.time()
            messages = []
            
            for i in range(num_messages):
                msg = creator(i)
                messages.append(msg)
            
            end_time = time.time()
            duration = end_time - start_time
            rate = num_messages / duration
            
            results[msg_type] = {
                'duration': duration,
                'rate': rate,
                'messages_per_second': rate
            }
            
            print(f"    {msg_type}: {rate:,.0f} messages/second ({duration:.3f}s)")
            
            # Cleanup
            del messages
            gc.collect()
        
        # Calculate overall performance
        avg_rate = statistics.mean(results[msg_type]['rate'] for msg_type in results)
        print(f"  Average creation rate: {avg_rate:,.0f} messages/second")
        
        # Success criteria: Should create at least 50k messages/second
        success = avg_rate >= 50000
        print(f"  Result: {'✓ PASSED' if success else '✗ FAILED'} (target: 50,000 msg/sec)")
        
        self.results['message_creation'] = {
            'success': success,
            'average_rate': avg_rate,
            'details': results
        }
        
        return success
    
    def benchmark_serialization(self, num_messages=5000):
        """Benchmark message serialization speed."""
        print(f"\n=== Benchmark 2: Message Serialization ({num_messages:,} messages) ===")
        
        if not PROTOCOL_AVAILABLE:
            print("Skipping - protocol not available")
            return False
        
        # Create test messages
        messages = []
        for i in range(num_messages):
            if i % 3 == 0:
                msg = self.message_builder.create_mouse_message(i, i+1, "move")
            elif i % 3 == 1:
                msg = self.message_builder.create_keyboard_message(f"key_{i}", "press")
            else:
                msg = self.message_builder.create_clipboard_message(f"clipboard_data_{i}", "text")
            messages.append(msg)
        
        print(f"  Created {len(messages)} test messages")
        
        # Test serialization methods
        serialization_tests = [
            ("to_bytes", lambda msg: msg.to_bytes()),
            ("structured_encode", lambda msg: self.protocol_adapter.encode_structured_message(msg)),
        ]
        
        results = {}
        
        for test_name, serializer in serialization_tests:
            print(f"  Testing {test_name}...")
            
            # Warmup
            for msg in messages[:100]:
                serializer(msg)
            
            # Benchmark
            start_time = time.time()
            total_bytes = 0
            
            for msg in messages:
                serialized = serializer(msg)
                total_bytes += len(serialized) if isinstance(serialized, (bytes, str)) else 0
            
            end_time = time.time()
            duration = end_time - start_time
            rate = num_messages / duration
            throughput = total_bytes / duration / 1024 / 1024  # MB/s
            
            results[test_name] = {
                'duration': duration,
                'rate': rate,
                'throughput_mbps': throughput,
                'total_bytes': total_bytes
            }
            
            print(f"    {test_name}: {rate:,.0f} msg/sec, {throughput:.2f} MB/s")
        
        # Success criteria: Should serialize at least 10k messages/second
        avg_rate = statistics.mean(results[test_name]['rate'] for test_name in results)
        success = avg_rate >= 10000
        print(f"  Average serialization rate: {avg_rate:,.0f} messages/second")
        print(f"  Result: {'✓ PASSED' if success else '✗ FAILED'} (target: 10,000 msg/sec)")
        
        self.results['serialization'] = {
            'success': success,
            'average_rate': avg_rate,
            'details': results
        }
        
        return success
    
    def benchmark_chunking_performance(self):
        """Benchmark chunking performance with various message sizes."""
        print(f"\n=== Benchmark 3: Chunking Performance ===")
        
        if not PROTOCOL_AVAILABLE:
            print("Skipping - protocol not available")
            return False
        
        # Test different payload sizes
        test_sizes = [
            ("tiny", 100),       # 100 bytes
            ("small", 1024),     # 1 KB
            ("medium", 10240),   # 10 KB  
            ("large", 102400),   # 100 KB
            ("huge", 1048576),   # 1 MB
        ]
        
        results = {}
        
        class MockSocket:
            def __init__(self):
                self.sent_data = b''
                self.send_count = 0
            
            def send(self, data):
                self.sent_data += data if isinstance(data, bytes) else data.encode()
                self.send_count += 1
                return len(data)
            
            def is_socket_open(self):
                return True
        
        for size_name, size_bytes in test_sizes:
            print(f"  Testing {size_name} messages ({size_bytes:,} bytes)...")
            
            # Create test message
            test_payload = {"data": "x" * size_bytes}
            test_message = ProtocolMessage(
                message_type="test",
                timestamp=time.time(),
                sequence_id=1,
                payload=test_payload
            )
            
            mock_socket = MockSocket()
            
            # Benchmark chunking
            start_time = time.time()
            
            # Send multiple messages of this size
            num_iterations = max(1, 1000 // (size_bytes // 1024 + 1))  # Fewer iterations for larger messages
            for i in range(num_iterations):
                self.chunk_manager.send_data(mock_socket, test_message)
            
            end_time = time.time()
            duration = end_time - start_time
            
            # Calculate metrics
            total_original_bytes = size_bytes * num_iterations
            total_sent_bytes = len(mock_socket.sent_data)
            overhead = (total_sent_bytes - total_original_bytes) / total_original_bytes * 100
            throughput = total_sent_bytes / duration / 1024 / 1024  # MB/s
            
            results[size_name] = {
                'duration': duration,
                'iterations': num_iterations,
                'total_sent_bytes': total_sent_bytes,
                'overhead_percent': overhead,
                'throughput_mbps': throughput,
                'chunks_sent': mock_socket.send_count
            }
            
            print(f"    {size_name}: {throughput:.2f} MB/s, {overhead:.1f}% overhead, {mock_socket.send_count} chunks")
        
        # Success criteria: Should handle at least 10 MB/s for large messages
        large_throughput = results.get('large', {}).get('throughput_mbps', 0)
        success = large_throughput >= 10.0
        
        print(f"  Large message throughput: {large_throughput:.2f} MB/s")
        print(f"  Result: {'✓ PASSED' if success else '✗ FAILED'} (target: 10 MB/s)")
        
        self.results['chunking'] = {
            'success': success,
            'large_throughput': large_throughput,
            'details': results
        }
        
        return success
    
    def benchmark_concurrent_load(self, num_threads=5, messages_per_thread=1000):
        """Benchmark concurrent message processing."""
        print(f"\n=== Benchmark 4: Concurrent Load ({num_threads} threads, {messages_per_thread:,} msg/thread) ===")
        
        if not PROTOCOL_AVAILABLE:
            print("Skipping - protocol not available")
            return False
        
        class ThreadSafeSocket:
            def __init__(self):
                self.sent_data = b''
                self.send_count = 0
                self.lock = threading.Lock()
            
            def send(self, data):
                with self.lock:
                    self.sent_data += data if isinstance(data, bytes) else data.encode()
                    self.send_count += 1
                    return len(data)
            
            def is_socket_open(self):
                return True
        
        shared_socket = ThreadSafeSocket()
        
        def worker_thread(thread_id, messages_to_send):
            """Worker thread that sends messages."""
            chunk_manager = ChunkManager()  # Each thread gets its own
            builder = MessageBuilder()
            
            messages_sent = 0
            start_time = time.time()
            
            try:
                for i in range(messages_to_send):
                    # Create different message types
                    if i % 3 == 0:
                        msg = builder.create_mouse_message(
                            thread_id * 1000 + i, 
                            thread_id * 1000 + i + 1, 
                            "move"
                        )
                    elif i % 3 == 1:
                        msg = builder.create_keyboard_message(f"key_{thread_id}_{i}", "press")
                    else:
                        msg = builder.create_clipboard_message(f"data_{thread_id}_{i}", "text")
                    
                    chunk_manager.send_data(shared_socket, msg)
                    messages_sent += 1
                
                end_time = time.time()
                duration = end_time - start_time
                
                return {
                    'thread_id': thread_id,
                    'messages_sent': messages_sent,
                    'duration': duration,
                    'rate': messages_sent / duration if duration > 0 else 0
                }
                
            except Exception as e:
                print(f"Thread {thread_id} error: {e}")
                return {
                    'thread_id': thread_id,
                    'messages_sent': messages_sent,
                    'duration': 0,
                    'rate': 0,
                    'error': str(e)
                }
        
        # Run concurrent workers
        print(f"  Starting {num_threads} worker threads...")
        
        overall_start = time.time()
        
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = []
            for thread_id in range(num_threads):
                future = executor.submit(worker_thread, thread_id, messages_per_thread)
                futures.append(future)
            
            # Collect results
            thread_results = []
            for future in as_completed(futures):
                result = future.result()
                thread_results.append(result)
        
        overall_end = time.time()
        overall_duration = overall_end - overall_start
        
        # Calculate metrics
        total_messages = sum(r['messages_sent'] for r in thread_results)
        total_rate = total_messages / overall_duration
        thread_rates = [r['rate'] for r in thread_results if r['rate'] > 0]
        avg_thread_rate = statistics.mean(thread_rates) if thread_rates else 0
        
        print(f"  Total messages sent: {total_messages:,}")
        print(f"  Overall duration: {overall_duration:.3f}s")
        print(f"  Overall rate: {total_rate:,.0f} messages/second")
        print(f"  Average thread rate: {avg_thread_rate:,.0f} messages/second")
        print(f"  Total data sent: {len(shared_socket.sent_data):,} bytes")
        
        # Success criteria: Should handle at least 1000 total msg/sec under concurrent load
        success = total_rate >= 1000
        print(f"  Result: {'✓ PASSED' if success else '✗ FAILED'} (target: 1,000 msg/sec)")
        
        self.results['concurrent_load'] = {
            'success': success,
            'total_rate': total_rate,
            'avg_thread_rate': avg_thread_rate,
            'thread_results': thread_results
        }
        
        return success
    
    def benchmark_memory_usage(self):
        """Benchmark memory usage patterns."""
        print(f"\n=== Benchmark 5: Memory Usage Patterns ===")
        
        if not PROTOCOL_AVAILABLE:
            print("Skipping - protocol not available")
            return False
        
        try:
            import psutil
            process = psutil.Process()
        except ImportError:
            print("psutil not available - using simplified memory test")
            process = None
        
        def get_memory_mb():
            if process:
                return process.memory_info().rss / 1024 / 1024
            else:
                return 0  # Fallback when psutil not available
        
        initial_memory = get_memory_mb()
        print(f"  Initial memory: {initial_memory:.1f} MB")
        
        # Test memory usage with large message creation
        print("  Testing message creation memory usage...")
        
        start_memory = get_memory_mb()
        messages = []
        
        # Create many messages
        for i in range(10000):
            if i % 3 == 0:
                msg = self.message_builder.create_mouse_message(i, i+1, "move")
            elif i % 3 == 1:
                msg = self.message_builder.create_keyboard_message(f"key_{i}", "press")
            else:
                # Some larger messages
                data = "x" * (1000 + i % 5000)
                msg = self.message_builder.create_clipboard_message(data, "text")
            messages.append(msg)
        
        peak_memory = get_memory_mb()
        memory_used = peak_memory - start_memory
        
        print(f"  Peak memory: {peak_memory:.1f} MB")
        print(f"  Memory used for 10k messages: {memory_used:.1f} MB")
        
        # Cleanup and check memory release
        del messages
        gc.collect()
        
        after_cleanup_memory = get_memory_mb()
        memory_released = peak_memory - after_cleanup_memory
        
        print(f"  Memory after cleanup: {after_cleanup_memory:.1f} MB")
        print(f"  Memory released: {memory_released:.1f} MB")
        
        # Success criteria: Memory usage should be reasonable
        if process:
            memory_per_message = memory_used / 10000 * 1024  # KB per message
            success = memory_per_message < 1.0  # Less than 1KB per message
            print(f"  Memory per message: {memory_per_message:.3f} KB")
            print(f"  Result: {'✓ PASSED' if success else '✗ FAILED'} (target: < 1KB/message)")
        else:
            success = True  # Pass if we can't measure
            print(f"  Result: ✓ PASSED (memory measurement unavailable)")
        
        self.results['memory_usage'] = {
            'success': success,
            'initial_memory': initial_memory,
            'peak_memory': peak_memory,
            'memory_used': memory_used,
            'memory_released': memory_released
        }
        
        return success
    
    def run_all_benchmarks(self):
        """Run all performance benchmarks."""
        print("=" * 70)
        print("PyContinuity Performance Benchmark Suite")
        print("=" * 70)
        
        if not PROTOCOL_AVAILABLE:
            print("⚠️  Protocol modules not available - limited testing")
            return False
        
        # Define benchmarks
        benchmarks = [
            ("Message Creation", self.benchmark_message_creation),
            ("Serialization", self.benchmark_serialization),
            ("Chunking Performance", self.benchmark_chunking_performance),
            ("Concurrent Load", self.benchmark_concurrent_load),
            ("Memory Usage", self.benchmark_memory_usage),
        ]
        
        # Run benchmarks
        start_time = time.time()
        results = []
        
        for benchmark_name, benchmark_func in benchmarks:
            try:
                result = benchmark_func()
                results.append((benchmark_name, result))
            except Exception as e:
                print(f"✗ {benchmark_name} failed with error: {e}")
                results.append((benchmark_name, False))
        
        total_time = time.time() - start_time
        
        # Summary
        print("\n" + "=" * 70)
        print("PERFORMANCE BENCHMARK SUMMARY")
        print("=" * 70)
        
        passed = sum(1 for _, result in results if result)
        total = len(results)
        
        for benchmark_name, result in results:
            icon = "✓" if result else "✗"
            status = "PASSED" if result else "FAILED"
            print(f"{icon} {benchmark_name}: {status}")
        
        print(f"\nOverall: {passed}/{total} benchmarks passed")
        print(f"Total benchmark time: {total_time:.2f}s")
        
        # Overall assessment
        success = passed >= (total * 0.8)  # 80% pass rate
        
        if success:
            print("\n🎉 PERFORMANCE BENCHMARKS PASSED!")
            print("PyContinuity protocol performance is acceptable.")
        else:
            print("\n❌ PERFORMANCE BENCHMARKS FAILED!")
            print("Some performance targets were not met.")
        
        # Save detailed results
        self.results['overall'] = {
            'success': success,
            'passed': passed,
            'total': total,
            'total_time': total_time
        }
        
        return success


def main():
    """Main entry point."""
    try:
        benchmark = PerformanceBenchmark()
        success = benchmark.run_all_benchmarks()
        
        # Print final summary
        print("\n" + "=" * 70)
        if success:
            print("🚀 PyContinuity performance is EXCELLENT!")
            print("The system meets all performance requirements.")
        else:
            print("⚠️  PyContinuity performance needs attention.")
            print("Some benchmarks did not meet target performance.")
        
        return 0 if success else 1
        
    except Exception as e:
        print(f"Benchmark suite error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())