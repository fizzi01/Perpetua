"""
Test for the async EventBus implementation.
"""
import asyncio
import time
from event.EventBus import AsyncEventBus
from utils.logging import Logger

Logger()

# Test event types
EVENT_TEST_1 = 1
EVENT_TEST_2 = 2



async def async_callback_1(data: dict):
    """Async callback for testing."""
    print(f"Async callback 1 received: {data}")
    await asyncio.sleep(0.1)  # Simulate some async work
    print("Async callback 1 completed")


async def async_callback_2(data: dict):
    """Another async callback for testing."""
    print(f"Async callback 2 received: {data}")
    await asyncio.sleep(0.05)  # Simulate some async work
    print("Async callback 2 completed")


def sync_callback(data: dict):
    """Sync callback for testing."""
    print(f"Sync callback received: {data}")
    time.sleep(0.05)  # Simulate some sync work
    print("Sync callback completed")


async def main():
    print("=" * 60)
    print("Testing AsyncEventBus Performance")
    print("=" * 60)

    event_bus = AsyncEventBus()

    # Subscribe callbacks
    event_bus.subscribe(EVENT_TEST_1, async_callback_1)
    event_bus.subscribe(EVENT_TEST_1, async_callback_2)
    event_bus.subscribe(EVENT_TEST_1, sync_callback)

    # Test 1: Dispatch and wait
    print("\n[Test 1] Dispatching event with await (all callbacks run concurrently)")
    start = time.time()
    await event_bus.dispatch(EVENT_TEST_1, data={"message": "Test 1", "value": 42})
    elapsed = time.time() - start
    print(f"✓ Test 1 completed in {elapsed:.3f}s (should be ~0.1s due to concurrency)")

    # Test 2: Fire and forget
    print("\n[Test 2] Dispatching event without waiting (fire and forget)")
    start = time.time()
    event_bus.dispatch_nowait(EVENT_TEST_1, data={"message": "Test 2", "value": 100})
    elapsed = time.time() - start
    print(f"✓ Test 2 dispatched in {elapsed:.3f}s (should be instant)")
    await asyncio.sleep(0.2)  # Wait for background task to complete

    # Test 3: Multiple events
    print("\n[Test 3] Dispatching multiple events concurrently")
    start = time.time()
    await asyncio.gather(
        event_bus.dispatch(EVENT_TEST_1, data={"message": "Test 3a"}),
        event_bus.dispatch(EVENT_TEST_1, data={"message": "Test 3b"}),
        event_bus.dispatch(EVENT_TEST_1, data={"message": "Test 3c"}),
    )
    elapsed = time.time() - start
    print(f"✓ Test 3 completed in {elapsed:.3f}s (3 events dispatched concurrently)")

    # Test 4: Performance test with many callbacks
    print("\n[Test 4] Performance test with 100 async callbacks")

    async def fast_callback(data: dict):
        await asyncio.sleep(0.001)

    # Subscribe 100 callbacks
    for i in range(100):
        event_bus.subscribe(EVENT_TEST_2, fast_callback)

    start = time.time()
    await event_bus.dispatch(EVENT_TEST_2, data={"test": "performance"})
    elapsed = time.time() - start
    print(f"✓ Test 4 completed in {elapsed:.3f}s (100 callbacks executed concurrently)")

    print("\n" + "=" * 60)
    print("All tests completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

