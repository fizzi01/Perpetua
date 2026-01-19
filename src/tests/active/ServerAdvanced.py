"""
Advanced Server Example - Gestione runtime degli stream
Dimostra come abilitare/disabilitare stream durante l'esecuzione
"""

import os
import sys

if sys.platform in ("win32", "cygwin", "cli"):
    import winloop as loop  # ty:ignore[unresolved-import]
else:
    import uvloop as loop
import asyncio
from socket import gethostname

from network.stream import StreamType
from service.server import Server
from config import ServerConfig, ApplicationConfig
from utils.logging import Logger


def helper():
    print("\nCommands:")
    print("  list    - List active streams")
    print("  enable  <stream> - Enable a stream")
    print("  disable <stream> - Disable a stream")
    print("  clients - Show connected clients")
    print("  add     - Add a new client")
    print("  remove  - Remove a client")
    print("  edit    - Edit a client's configuration")
    print("  ssl on/off - Enable or disable SSL")
    print("  share ca - Share CA certificate with clients")
    print("  help    - Show this help message")
    print("  quit    - Stop server and exit\n")


print(os.getcwd())


async def ainput(prompt: str = "", timeout: float = 0.1) -> str:
    task = asyncio.to_thread(input, prompt)
    try:
        return await asyncio.wait_for(task, timeout)
    except asyncio.TimeoutError:
        return ""


async def interactive_server():
    """Server interattivo con controllo runtime degli stream"""

    # Configurazione
    app_config = ApplicationConfig()
    app_config.config_path = "_test_config/"
    server_config = ServerConfig(app_config, config_file=None)

    # Set connection parameters
    server_config.set_connection_params(host=gethostname(), port=5555)

    # Enable SSL
    server_config.enable_ssl()

    # Set logging
    server_config.set_logging(level=Logger.INFO)

    server = Server(
        app_config=app_config, server_config=server_config, auto_load_config=True
    )

    # Aggiungi client
    # server.add_client(hostname="Federico", screen_position="top")
    # server.add_client(hostname="Test2", screen_position="bottom")
    # server.add_client(hostname="Test3", ip_address="192.168.1.12", screen_position="left")

    # Avvia server
    if not await server.start():
        print("Failed to start server")
        return

    print("\n" + "=" * 60)
    print("Server")
    print("=" * 60)
    helper()
    print("=" * 60 + "\n")

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
                        print(
                            f"  - {client.get_net_id()} ({client.screen_position}) - {status}"
                        )
                    print()
                elif cmd == "add":
                    # Dynamic add client
                    hostname = input("Enter client hostname or IP: ").strip()
                    position = (
                        input("Enter screen position (top/bottom/left/right): ")
                        .strip()
                        .lower()
                    )
                    await server.add_client(
                        hostname=hostname, ip_address=hostname, screen_position=position
                    )
                    print(f"Client {hostname} added at position {position}\n")
                elif cmd == "remove":
                    # Dynamic remove client
                    ip = input("Enter client hostname or IP to remove: ").strip()
                    await server.remove_client(hostname=ip, ip_address=ip)
                    print(f"Client {ip} removed\n")
                elif cmd == "edit":
                    # Dynamic edit client
                    hostname = input("Enter client hostname or IP: ").strip()
                    position = (
                        input("Enter new screen position (top/bottom/left/right): ")
                        .strip()
                        .lower()
                    )
                    await server.edit_client(
                        hostname=hostname,
                        ip_address=hostname,
                        new_screen_position=position,
                    )
                    print(f"Client {hostname} updated to position {position}\n")
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
                elif cmd.startswith("ssl on"):
                    server.enable_ssl()
                elif cmd.startswith("ssl off"):
                    server.disable_ssl()
                elif cmd.startswith("share ca"):
                    await server.share_certificate(host=server.config.host)
                elif cmd == "help":
                    helper()
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


async def main():
    """Entry point"""
    await interactive_server()


if __name__ == "__main__":
    try:
        loop.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
    except RuntimeError as e:
        print(f"\nRuntime error: {e}")
