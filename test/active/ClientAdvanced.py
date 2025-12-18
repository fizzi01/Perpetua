"""
Advanced Client Example - Runtime management of streams and SSL
Demonstrates how to enable/disable streams during execution and manage SSL certificates
"""
import uvloop
import asyncio
from socket import gethostname

from network.stream import StreamType
from service.client import Client
from config import ClientConfig, ApplicationConfig
from utils.logging import Logger


def helper():
    print("\nCommands:")
    print("  status    - Show connection status and active streams")
    print("  list      - List enabled streams")
    print("  enable  <stream> - Enable a stream (use number: 1=MOUSE, 2=KEYBOARD, 3=CLIPBOARD)")
    print("  disable <stream> - Disable a stream")
    print("  ssl       - Show SSL status")
    print("  cert      - Receive certificate from server (interactive)")
    print("  connect   - Connect to server")
    print("  disconnect - Disconnect from server")
    print("  reconnect - Reconnect to server")
    print("  quit      - Stop client and exit\n")


async def interactive_client():
    """Interactive client with runtime control of streams and SSL"""

    # Configuration
    app_config = ApplicationConfig()
    app_config.config_path = "_test_config/"
    client_config = ClientConfig(app_config, config_file=None)

    # Set server connection
    client_config.set_server_connection(
        host=input("Enter server host (default: localhost): ").strip() or "localhost",
        port=int(input("Enter server port (default: 5555): ").strip() or "5555"),
        auto_reconnect=True
    )
    client_config.set_hostname(gethostname())
    # Set logging
    client_config.set_logging(level=Logger.DEBUG)

    client = Client(
        app_config=app_config,
        client_config=client_config,
        auto_load_config=False
    )

    # Enable default streams
    await client.enable_stream(StreamType.MOUSE)
    await client.enable_stream(StreamType.KEYBOARD)
    await client.enable_stream(StreamType.CLIPBOARD)

    print("\n" + "="*60)
    print("PyContinuity Interactive Client")
    print("="*60)
    helper()
    print("="*60 + "\n")

    # Ask if user wants to start client immediately
    start_now = input("Start client now? (y/n, default: y): ").strip().lower()
    if start_now != 'n':
        print("\nStarting client...")
        if not await client.start():
            print("Failed to start client")
            return
        print("Client started successfully!")
    else:
        print("\nClient created but not started. Use 'connect' command to start.")

    # Task to handle user input
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

                elif cmd == "status":
                    print(f"\n{'='*40}")
                    print(f"Connection Status:")
                    print(f"  Running: {client.is_running()}")
                    print(f"  Connected: {client.is_connected()}")
                    print(f"  Server: {client.config.get_server_host()}:{client.config.get_server_port()}")
                    print(f"  Auto-reconnect: {client.config.do_auto_reconnect()}")
                    print(f"\nStreams:")
                    print(f"  Enabled: {client.get_enabled_streams()}")
                    print(f"  Active: {client.get_active_streams()}")
                    print(f"\nSSL:")
                    print(f"  Certificate loaded: {client.has_certificate()}")
                    if client.has_certificate():
                        print(f"  Certificate path: {client.get_certificate_path()}")
                    print(f"{'='*40}\n")

                elif cmd == "list":
                    print(f"\nEnabled streams: {client.get_enabled_streams()}")
                    print(f"Active streams: {client.get_active_streams()}\n")

                elif cmd == "ssl":
                    print(f"\nSSL Status:")
                    print(f"  Certificate loaded: {client.has_certificate()}")
                    if client.has_certificate():
                        print(f"  Certificate path: {client.get_certificate_path()}")
                    else:
                        print("  No certificate loaded. Use 'cert' command to receive one.")
                    print()

                elif cmd == "cert":
                    print("\n" + "="*60)
                    print("Certificate Reception")
                    print("="*60)
                    print("\nSteps:")
                    print("1. On the server, run: share ca")
                    print("2. The server will display a 6-digit OTP")
                    print("3. Enter that OTP below\n")

                    cert_host = input(f"Certificate server host (default: {client.config.get_server_host()}): ").strip()
                    if not cert_host:
                        cert_host = client.config.get_server_host()

                    cert_port = input("Certificate server port (default: 5556): ").strip()
                    cert_port = int(cert_port) if cert_port else 5556

                    otp = input("\nEnter 6-digit OTP from server: ").strip()

                    if not otp or len(otp) != 6 or not otp.isdigit():
                        print("✗ Invalid OTP format. Must be 6 digits.\n")
                        continue

                    print("\nReceiving certificate...")
                    success = await client.receive_certificate(
                        otp=otp,
                        server_host=cert_host,
                        server_port=cert_port
                    )

                    if success:
                        print("✓ Certificate received successfully!")
                        print(f"  Saved to: {client.get_certificate_path()}")

                        # Ask if user wants to enable SSL now
                        if not client.is_running():
                            enable = input("\nEnable SSL now? (y/n, default: y): ").strip().lower()
                            if enable != 'n':
                                if client.enable_ssl():
                                    print("✓ SSL enabled")
                                else:
                                    print("✗ Failed to enable SSL")
                        else:
                            print("\nNote: Client is running. SSL will be used on next connection.")
                    else:
                        print("✗ Failed to receive certificate")
                    print()

                elif cmd == "connect":
                    if client.is_running():
                        print("\n✗ Client already running. Use 'disconnect' first if you want to reconnect.\n")
                        continue

                    print("\nConnecting to server...")
                    if await client.start():
                        print("✓ Client started and connecting...")
                        # Wait a bit for connection
                        await asyncio.sleep(2)
                        if client.is_connected():
                            print(f"✓ Connected! Active streams: {client.get_active_streams()}")
                        else:
                            print("⧗ Connection in progress...")
                    else:
                        print("✗ Failed to start client")
                    print()

                elif cmd == "disconnect":
                    if not client.is_running():
                        print("\n✗ Client not running\n")
                        continue

                    print("\nDisconnecting...")
                    await client.stop()
                    print("✓ Client stopped\n")

                elif cmd == "reconnect":
                    print("\nReconnecting...")
                    if client.is_running():
                        await client.stop()
                        await asyncio.sleep(1)

                    if await client.start():
                        print("✓ Client restarted and connecting...")
                        await asyncio.sleep(2)
                        if client.is_connected():
                            print(f"✓ Connected! Active streams: {client.get_active_streams()}")
                    else:
                        print("✗ Failed to restart client")
                    print()

                elif cmd.startswith("enable "):
                    try:
                        stream_type = int(cmd.split()[1])
                    except (ValueError, IndexError):
                        print("✗ Invalid stream type. Use: 1=MOUSE, 4=KEYBOARD, 12=CLIPBOARD\n")
                        continue

                    stream_names = {1: "MOUSE", 4: "KEYBOARD", 12: "CLIPBOARD"}
                    stream_name = stream_names.get(stream_type, str(stream_type))

                    print(f"\nEnabling {stream_name} stream...")
                    try:
                        if client.is_running() and client.is_connected():
                            success = await client.enable_stream_runtime(stream_type)
                        else:
                            await client.enable_stream(stream_type)
                            success = True

                        if success:
                            print(f"✓ {stream_name} stream enabled")
                            print(f"  Enabled streams: {client.get_enabled_streams()}")
                            if client.is_connected():
                                print(f"  Active streams: {client.get_active_streams()}")
                        else:
                            print(f"✗ Failed to enable {stream_name} stream")
                    except Exception as e:
                        print(f"✗ Error enabling {stream_name} stream: {e}")
                    print()

                elif cmd.startswith("disable "):
                    try:
                        stream_type = int(cmd.split()[1])
                    except (ValueError, IndexError):
                        print("✗ Invalid stream type. Use: 1=MOUSE, 4=KEYBOARD, 12=CLIPBOARD\n")
                        continue

                    if stream_type == 0:  # COMMAND stream
                        print("✗ Cannot disable COMMAND stream (it's always enabled)\n")
                        continue

                    stream_names = {1: "MOUSE", 4: "KEYBOARD", 12: "CLIPBOARD"}
                    stream_name = stream_names.get(stream_type, str(stream_type))

                    print(f"\nDisabling {stream_name} stream...")
                    try:
                        if client.is_running() and client.is_connected():
                            success = await client.disable_stream_runtime(stream_type)
                        else:
                            await client.disable_stream(stream_type)
                            success = True

                        if success:
                            print(f"✓ {stream_name} stream disabled")
                            print(f"  Enabled streams: {client.get_enabled_streams()}")
                            if client.is_connected():
                                print(f"  Active streams: {client.get_active_streams()}")
                        else:
                            print(f"✗ Failed to disable {stream_name} stream")
                    except Exception as e:
                        print(f"✗ Error disabling {stream_name} stream: {e}")
                    print()

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
                import traceback
                traceback.print_exc()

        return "quit"

    # Execute command handler
    try:
        result = await handle_commands()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received")
    finally:
        if client.is_running():
            await client.stop()
        print("Client stopped")


async def main():
    """Entry point"""
    import sys

    print("\n" + "="*60)
    print("PyContinuity Advanced Client Examples")
    print("="*60)

    await interactive_client()


if __name__ == "__main__":
    try:
        uvloop.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
    except RuntimeError as e:
        print(f"\nRuntime error: {e}")

