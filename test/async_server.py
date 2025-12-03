from network.connection.ServerConnectionServices import AsyncServerConnectionHandler
import asyncio

async def main():
    handler = AsyncServerConnectionHandler(host="0.0.0.0", port=5001)
    await handler.start()

    try:
        await asyncio.Event().wait()  # Run indefinitely
    except KeyboardInterrupt:
        await handler.stop()


asyncio.run(main())
