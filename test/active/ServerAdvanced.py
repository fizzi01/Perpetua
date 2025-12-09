"""
Advanced Server Example - Gestione runtime degli stream
Dimostra come abilitare/disabilitare stream durante l'esecuzione
"""
import asyncio

from network.stream import StreamType
from service.server import Server, ServerConnectionConfig
from utils.logging import Logger


async def interactive_server():
    """Server interattivo con controllo runtime degli stream"""

    # Configurazione
    conn_config = ServerConnectionConfig(
        host="192.168.1.62",
        port=5555
    )

    server = Server(
        connection_config=conn_config,
        log_level=Logger.INFO
    )

    # Aggiungi client
    server.add_client("192.168.1.74", screen_position="top")

    # Avvia server
    if not await server.start():
        print("Failed to start server")
        return

    print("\n" + "="*60)
    print("PyContinuity Interactive Server")
    print("="*60)
    print("\nCommands:")
    print("  list    - List active streams")
    print("  enable  <stream> - Enable a stream (mouse, keyboard, clipboard)")
    print("  disable <stream> - Disable a stream")
    print("  clients - Show connected clients")
    print("  quit    - Stop server and exit")
    print("="*60 + "\n")

    # Task per gestire input utente
    async def handle_commands():
        loop = asyncio.get_event_loop()

        while True:
            try:
                # Read input in executor to avoid blocking
                cmd = await loop.run_in_executor(None, input, "> ")
                cmd = cmd.strip().lower()

                if cmd == "quit" or cmd == "exit":
                    print("\nShutting down...")
                    break

                elif cmd == "list":
                    print(f"\nActive streams: {server.get_active_streams()}")
                    print(f"Enabled streams: {server.get_enabled_streams()}\n")

                elif cmd == "clients":
                    clients = server.get_clients()
                    print(f"\nRegistered clients: {len(clients)}")
                    for client in clients:
                        status = "Connected" if client.is_connected else "Disconnected"
                        print(f"  - {client.ip_address} ({client.screen_position}) - {status}")
                    print()
                elif cmd == "add":
                    # Dynamic add client
                    ip = input("Enter client IP address: ").strip()
                    position = input("Enter screen position (top/bottom/left/right): ").strip().lower()
                    server.add_client(ip, screen_position=position)
                    print(f"Client {ip} added at position {position}\n")
                elif cmd == "remove":
                    # Dynamic remove client
                    ip = input("Enter client IP address to remove: ").strip()
                    await server.remove_client(ip)
                    print(f"Client {ip} removed\n")
                elif cmd == "edit":
                    # Dynamic edit client
                    ip = input("Enter client IP address to edit: ").strip()
                    position = input("Enter new screen position (top/bottom/left/right): ").strip().lower()
                    server.edit_client(ip, screen_position=position)
                    print(f"Client {ip} updated to position {position}\n")
                elif cmd.startswith("enable "):
                    stream_type = cmd.split()[1]
                    # Parse to int
                    stream_type = int(stream_type)
                    print(f"\nEnabling {stream_type}...")
                    try:
                        await server.enable_stream_runtime(stream_type)
                        print(f"✓ {stream_type} enabled")
                        print(f"Active streams: {server.get_active_streams()}\n")
                    except Exception as e:
                        print(f"✗ Failed to enable {stream_type}: {e}\n")

                elif cmd.startswith("disable "):
                    stream_type = cmd.split()[1]
                    print(f"\nDisabling {stream_type}...")
                    stream_type = int(stream_type)
                    try:
                        await server.disable_stream_runtime(stream_type)
                        print(f"✓ {stream_type} disabled")
                        print(f"Active streams: {server.get_active_streams()}\n")
                    except Exception as e:
                        print(f"✗ Failed to disable {stream_type}: {e}\n")

                elif cmd == "help":
                    print("\nCommands:")
                    print("  list    - List active streams")
                    print("  enable  <stream> - Enable a stream")
                    print("  disable <stream> - Disable a stream")
                    print("  clients - Show connected clients")
                    print("  quit    - Stop server and exit\n")

                elif cmd:
                    print(f"Unknown command: {cmd}")
                    print("Type 'help' for available commands\n")

            except EOFError:
                print("\nEOF received, shutting down...")
                break
            except Exception as e:
                print(f"Error: {e}\n")

        return "quit"

    # Esegui comando handler
    try:
        result = await handle_commands()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received")
    finally:
        await server.stop()
        print("Server stopped")


async def automated_demo():
    """Demo automatica che mostra il toggle degli stream"""

    conn_config = ServerConnectionConfig(
        host="192.168.1.62",
        port=5555
    )

    server = Server(
        connection_config=conn_config,
        log_level=Logger.INFO
    )

    server.add_client("192.168.1.74", screen_position="top")

    if not await server.start():
        print("Failed to start server")
        return

    print("\n" + "="*60)
    print("Automated Stream Toggle Demo")
    print("="*60 + "\n")

    try:
        print("Initial state:")
        print(f"  Active streams: {server.get_active_streams()}\n")
        await asyncio.sleep(3)

        # Disabilita mouse
        print("Disabling mouse stream...")
        await server.disable_stream_runtime(StreamType.MOUSE)
        print(f"  Active streams: {server.get_active_streams()}\n")
        await asyncio.sleep(3)

        # Disabilita keyboard
        print("Disabling keyboard stream...")
        await server.disable_stream_runtime(StreamType.KEYBOARD)
        print(f"  Active streams: {server.get_active_streams()}\n")
        await asyncio.sleep(3)

        # Riabilita mouse
        print("Re-enabling mouse stream...")
        await server.enable_stream_runtime(StreamType.MOUSE)
        print(f"  Active streams: {server.get_active_streams()}\n")
        await asyncio.sleep(3)

        # Riabilita keyboard
        print("Re-enabling keyboard stream...")
        await server.enable_stream_runtime(StreamType.KEYBOARD)
        print(f"  Active streams: {server.get_active_streams()}\n")
        await asyncio.sleep(3)

        print("Demo completed. Server will continue running...")
        print("Press Ctrl+C to stop\n")

        # Mantieni in esecuzione
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\nKeyboard interrupt received")
    finally:
        await server.stop()
        print("Server stopped")


async def main():
    """Entry point"""
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        await automated_demo()
    else:
        await interactive_server()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
    except RuntimeError as e:
        print(f"\nRuntime error: {e}")

