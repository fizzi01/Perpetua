"""
Example: Using Async Stream Handlers with AsyncEventBus

This example demonstrates how to use the new async stream handlers
with the optimized AsyncEventBus.
"""
import asyncio
from event.EventBus import AsyncEventBus
from event import EventType, MouseEvent
from model.ClientObj import ClientsManager, ClientObj
from network.stream.ServerCustomStream import UnidirectionalStreamHandler, BidirectionalStreamHandler
from utils.logging import Logger

Logger(stdout=print, logging=True)

async def message_callback(message_data: dict):
    """
    Example async callback for processing received messages.
    This callback is automatically invoked by MessageExchange when a message is received.
    """
    print(f"[Callback] Received message: {message_data}")
    # Simulate some async processing
    await asyncio.sleep(0.01)
    print(f"[Callback] Processed message type: {message_data.get('type')}")


async def example_unidirectional_stream():
    """
    Example: Unidirectional stream handler (Server -> Client)
    """
    print("\n" + "="*60)
    print("Example: Unidirectional Stream Handler")
    print("="*60)

    # Setup
    event_bus = AsyncEventBus()
    clients = ClientsManager()

    # Create stream handler
    stream_handler = UnidirectionalStreamHandler(
        stream_type=1,  # Example: mouse stream
        clients=clients,
        event_bus=event_bus,
        handler_id="MouseStreamHandler",
        source="server",
        sender=True
    )

    # Register callback for receiving messages
    stream_handler.register_receive_callback(
        receive_callback=message_callback,
        message_type="mouse_event"
    )

    # Start the stream handler
    await stream_handler.start()
    print("✓ Stream handler started")

    # Simulate sending messages
    for i in range(5):
        await stream_handler.send(MouseEvent(
            x=100 + i * 10,
            y=200 + i * 10,
            button=0,
            action="click")
        )
        print(f"✓ Sent message {i+1}/5")
        await asyncio.sleep(0.1)

    # Wait a bit for processing
    await asyncio.sleep(0.5)

    # Stop the stream handler
    await stream_handler.stop()
    print("✓ Stream handler stopped")


async def example_eventbus_performance():
    """
    Example: AsyncEventBus high-performance concurrent dispatch
    """
    print("\n" + "="*60)
    print("Example: AsyncEventBus Performance")
    print("="*60)

    event_bus = AsyncEventBus()

    # Stats tracking
    callback_count = [0]

    async def fast_async_callback(data: dict):
        """Fast async callback for performance testing."""
        callback_count[0] += 1
        await asyncio.sleep(0.001)  # Simulate minimal async work

    def sync_callback(data: dict):
        """Sync callback that runs in executor."""
        callback_count[0] += 1

    # Subscribe 50 async + 50 sync callbacks
    for i in range(500):
        event_bus.subscribe(EventType.CLIENT_CONNECTED, fast_async_callback)
        event_bus.subscribe(EventType.CLIENT_CONNECTED, sync_callback)

    print(f"Subscribed 100 callbacks (50 async + 50 sync)")

    # Test concurrent dispatch
    import time
    start = time.time()

    # Dispatch 10 events concurrently
    await asyncio.gather(*[
        event_bus.dispatch(EventType.CLIENT_CONNECTED, data={"client_id": i})
        for i in range(10)
    ])

    elapsed = time.time() - start
    print(f"✓ Dispatched 10 events with 100 callbacks each")
    print(f"✓ Total callbacks executed: {callback_count[0]}")
    print(f"✓ Time elapsed: {elapsed:.3f}s")
    print(f"✓ Callbacks/second: {callback_count[0]/elapsed:.0f}")


async def example_fire_and_forget():
    """
    Example: Fire-and-forget event dispatch
    """
    print("\n" + "="*60)
    print("Example: Fire-and-Forget Dispatch")
    print("="*60)

    event_bus = AsyncEventBus()

    async def slow_callback(data: dict):
        """A slow callback that takes time to process."""
        await asyncio.sleep(1)
        print(f"✓ Slow callback finished processing: {data}")

    event_bus.subscribe(EventType.CLIENT_ACTIVE, slow_callback)

    print("Dispatching event without waiting (fire-and-forget)...")
    import time
    start = time.time()

    # Fire and forget - doesn't wait for callback completion
    event_bus.dispatch_nowait(EventType.CLIENT_ACTIVE, data={"message": "Client activated"})

    elapsed = time.time() - start
    print(f"✓ Dispatch returned immediately in {elapsed:.3f}s")
    print("(Callback is still running in background)")

    # Wait for background task to complete
    await asyncio.sleep(1.5)


async def example_mixed_callbacks():
    """
    Example: Mix of sync and async callbacks
    """
    print("\n" + "="*60)
    print("Example: Mixed Sync/Async Callbacks")
    print("="*60)

    event_bus = AsyncEventBus()

    async def async_handler(data: dict):
        """Async callback."""
        print(f"[Async] Processing: {data.get('action')}")
        await asyncio.sleep(0.05)
        print(f"[Async] Completed: {data.get('action')}")

    def sync_handler(data: dict):
        """Sync callback - will run in executor."""
        print(f"[Sync] Processing: {data.get('action')}")
        import time
        time.sleep(0.05)
        print(f"[Sync] Completed: {data.get('action')}")

    # Subscribe both types
    event_bus.subscribe(EventType.CLIENT_DISCONNECTED, async_handler)
    event_bus.subscribe(EventType.CLIENT_DISCONNECTED, sync_handler)

    import time
    start = time.time()

    # Dispatch - both callbacks run concurrently
    await event_bus.dispatch(
        EventType.CLIENT_DISCONNECTED,
        data={"action": "cleanup_resources"}
    )

    elapsed = time.time() - start
    print(f"✓ Both callbacks completed in {elapsed:.3f}s (ran concurrently)")


async def main():
    """
    Run all examples.
    """
    print("\n" + "="*60)
    print("ASYNC STREAM HANDLERS & EVENTBUS EXAMPLES")
    print("="*60)

    # Note: example_unidirectional_stream() requires actual socket connections
    # So we skip it in this demo and focus on EventBus examples

    await example_eventbus_performance()
    await example_fire_and_forget()
    await example_mixed_callbacks()

    print("\n" + "="*60)
    print("ALL EXAMPLES COMPLETED!")
    print("="*60)
    print("\nKey Takeaways:")
    print("1. All callbacks run concurrently for maximum performance")
    print("2. Both sync and async callbacks are supported automatically")
    print("3. Fire-and-forget dispatch for non-critical events")
    print("4. Stream handlers are now fully async and integrate with MessageExchange")
    print("5. No threading overhead - pure asyncio for scalability")


if __name__ == "__main__":
    asyncio.run(main())

