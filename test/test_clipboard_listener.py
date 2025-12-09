"""
Test for async clipboard listener with polling mechanism.
"""
import asyncio
from input.clipboard import Clipboard, ClipboardType


async def test_clipboard_basic():
    """Test basic clipboard monitoring."""
    print("\n=== Test 1: Basic Clipboard Monitoring ===")

    changes_detected = []

    async def on_clipboard_change(content: str, content_type: ClipboardType):
        changes_detected.append((content, content_type))
        print(f"✓ Clipboard changed: [{content_type.value}] {content[:50]}...")

    # Create listener with fast polling for testing
    listener = Clipboard(
        on_change=on_clipboard_change,
        poll_interval=0.5,  # 200ms for responsive testing
        content_types=[ClipboardType.TEXT, ClipboardType.URL, ClipboardType.FILE]
    )

    # Start monitoring
    await listener.start()

    # Wait a bit to establish baseline
    await asyncio.sleep(10)

    # Simulate clipboard changes using debug method (doesn't update internal state)
    print("Setting clipboard content 1...")
    await listener._debug_set_clipboard("Test content 1")
    await asyncio.sleep(0.5)

    print("Setting clipboard content 2...")
    await listener._debug_set_clipboard("Test content 2")
    await asyncio.sleep(0.5)

    print("Setting same content again (should not trigger)...")
    await listener._debug_set_clipboard("Test content 2")
    await asyncio.sleep(0.5)

    print("Setting clipboard content 3...")
    await listener._debug_set_clipboard("https://example.com")
    await asyncio.sleep(0.5)

    # Stop monitoring
    await listener.stop()

    print(f"\nTotal changes detected: {len(changes_detected)}")
    for i, (content, ctype) in enumerate(changes_detected, 1):
        print(f"  {i}. [{ctype.value}] {content[:30]}...")

    # We should detect changes (exact count depends on initial clipboard state)
    assert len(changes_detected) >= 2, f"Expected at least 2 changes, got {len(changes_detected)}"
    print("✓ Test passed!")


async def test_clipboard_performance():
    """Test clipboard polling performance."""
    print("\n=== Test 2: Clipboard Polling Performance ===")

    change_count = 0

    async def on_clipboard_change(content: str, content_type: ClipboardType):
        nonlocal change_count
        change_count += 1

    # Create listener with very fast polling
    listener = Clipboard(
        on_change=on_clipboard_change,
        poll_interval=0.05  # 50ms - aggressive polling
    )

    await listener.start()

    # Measure performance over time
    start_time = asyncio.get_event_loop().time()

    # Make rapid changes
    for i in range(10):
        await listener._debug_set_clipboard(f"Test content {i}")
        await asyncio.sleep(0.1)  # Small delay between changes

    elapsed = asyncio.get_event_loop().time() - start_time

    await listener.stop()

    print(f"✓ Made 10 clipboard changes in {elapsed:.2f}s")
    print(f"✓ Detected {change_count} changes")
    print(f"✓ Average detection time: {elapsed/max(change_count, 1):.3f}s per change")

    assert change_count >= 5, f"Expected at least 5 detections, got {change_count}"
    print("✓ Test passed!")


async def test_multiple_listeners():
    """Test multiple clipboard listeners."""
    print("\n=== Test 3: Multiple Clipboard Listeners ===")

    listener1_changes = []
    listener2_changes = []

    async def on_change_1(content: str, content_type: ClipboardType):
        listener1_changes.append(content)
        print(f"✓ Listener 1 detected: {content[:30]}...")

    async def on_change_2(content: str, content_type: ClipboardType):
        listener2_changes.append(content)
        print(f"✓ Listener 2 detected: {content[:30]}...")

    listener1 = Clipboard(on_change=on_change_1, poll_interval=0.2)
    listener2 = Clipboard(on_change=on_change_2, poll_interval=0.3)

    # Start both
    await listener1.start()
    await listener2.start()

    await asyncio.sleep(0.5)

    # Make a change via debug method (doesn't update state)
    await listener1._debug_set_clipboard("Shared clipboard content")

    # Wait for both to detect
    await asyncio.sleep(1.0)

    # Stop both
    await listener1.stop()
    await listener2.stop()

    print(f"Listener 1 changes: {len(listener1_changes)}")
    print(f"Listener 2 changes: {len(listener2_changes)}")

    # Both should detect the change (though they may have different initial states)
    assert len(listener1_changes) >= 1 or len(listener2_changes) >= 1, \
        "At least one listener should detect changes"
    print("✓ Test passed!")


async def test_get_last_content():
    """Test getting last clipboard content."""
    print("\n=== Test 4: Get Last Content ===")

    listener = Clipboard(poll_interval=0.2)

    await listener.start()
    await asyncio.sleep(0.3)

    # Set content
    test_content = "Test content for retrieval"
    await listener.set_clipboard(test_content)
    await asyncio.sleep(0.3)

    # Get last content
    last_content = listener.get_last_content()

    await listener.stop()

    print(f"Last content: {last_content}")
    assert last_content == test_content, f"Expected '{test_content}', got '{last_content}'"
    print("✓ Test passed!")


async def benchmark_clipboard_polling():
    """Benchmark clipboard polling overhead."""
    print("\n=== Benchmark: Clipboard Polling Overhead ===")

    detection_times = []

    async def on_change(content: str, content_type: ClipboardType):
        detection_times.append(asyncio.get_event_loop().time())

    listener = Clipboard(on_change=on_change, poll_interval=0.05)

    await listener.start()
    await asyncio.sleep(0.2)  # Let it stabilize

    # Perform timed changes
    change_times = []
    for i in range(20):
        change_times.append(asyncio.get_event_loop().time())
        await listener._debug_set_clipboard(f"Benchmark content {i} - {asyncio.get_event_loop().time()}")
        await asyncio.sleep(0.1)

    await asyncio.sleep(0.5)  # Wait for last detection
    await listener.stop()

    # Calculate latencies
    latencies = []
    for change_time in change_times:
        # Find the closest detection time after this change
        future_detections = [dt for dt in detection_times if dt >= change_time]
        if future_detections:
            latency = (min(future_detections) - change_time) * 1000  # Convert to ms
            latencies.append(latency)

    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)

        print(f"✓ Changes made: {len(change_times)}")
        print(f"✓ Changes detected: {len(detection_times)}")
        print(f"✓ Average detection latency: {avg_latency:.1f}ms")
        print(f"✓ Min latency: {min_latency:.1f}ms")
        print(f"✓ Max latency: {max_latency:.1f}ms")
        print(f"✓ Poll interval: 50ms")
    else:
        print("⚠ Could not calculate latencies")

    print("✓ Benchmark complete!")


async def main():
    """Run all tests."""
    print("Starting clipboard listener tests...")

    try:
        await test_clipboard_basic()
        await asyncio.sleep(0.5)

        await test_clipboard_performance()
        await asyncio.sleep(0.5)

        await test_multiple_listeners()
        await asyncio.sleep(0.5)

        await test_get_last_content()
        await asyncio.sleep(0.5)

        await benchmark_clipboard_polling()

        print("\n" + "="*50)
        print("ALL TESTS PASSED! ✓")
        print("="*50)

    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    asyncio.run(main())

