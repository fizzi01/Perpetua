"""
Advanced Client Example - Runtime management of streams and SSL
Demonstrates how to enable/disable streams during execution and manage SSL certificates
"""
import asyncio
from socket import gethostname

from network.stream import StreamType
from service.client import Client
from config import ClientConnectionConfig
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
    conn_config = ClientConnectionConfig(
        server_host=input("Enter server host (default: localhost): ").strip() or "localhost",
        server_port=int(input("Enter server port (default: 5555): ").strip() or "5555"),
        client_hostname=gethostname(),
        auto_reconnect=True
    )

    client = Client(
        connection_config=conn_config,
        log_level=Logger.DEBUG
    )

    # Enable default streams
    client.enable_stream(StreamType.MOUSE)
    client.enable_stream(StreamType.KEYBOARD)
    client.enable_stream(StreamType.CLIPBOARD)

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
                    print(f"  Server: {client.connection_config.server_host}:{client.connection_config.server_port}")
                    print(f"  Auto-reconnect: {client.connection_config.auto_reconnect}")
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

                    cert_host = input(f"Certificate server host (default: {conn_config.server_host}): ").strip()
                    if not cert_host:
                        cert_host = conn_config.server_host

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
                        print("✗ Invalid stream type. Use: 1=MOUSE, 2=KEYBOARD, 3=CLIPBOARD\n")
                        continue

                    stream_names = {1: "MOUSE", 2: "KEYBOARD", 3: "CLIPBOARD"}
                    stream_name = stream_names.get(stream_type, str(stream_type))

                    print(f"\nEnabling {stream_name} stream...")
                    try:
                        if client.is_running() and client.is_connected():
                            success = await client.enable_stream_runtime(stream_type)
                        else:
                            client.enable_stream(stream_type)
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
                        print("✗ Invalid stream type. Use: 1=MOUSE, 2=KEYBOARD, 3=CLIPBOARD\n")
                        continue

                    if stream_type == 0:  # COMMAND stream
                        print("✗ Cannot disable COMMAND stream (it's always enabled)\n")
                        continue

                    stream_names = {1: "MOUSE", 2: "KEYBOARD", 3: "CLIPBOARD"}
                    stream_name = stream_names.get(stream_type, str(stream_type))

                    print(f"\nDisabling {stream_name} stream...")
                    try:
                        if client.is_running() and client.is_connected():
                            success = await client.disable_stream_runtime(stream_type)
                        else:
                            client.disable_stream(stream_type)
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


async def automated_demo():
    """Automated demo showing stream toggling"""

    print("\n" + "="*60)
    print("Automated Client Stream Toggle Demo")
    print("="*60 + "\n")

    # Get server info
    server_host = input("Enter server host (default: localhost): ").strip() or "localhost"
    server_port = int(input("Enter server port (default: 5555): ").strip() or "5555")

    conn_config = ClientConnectionConfig(
        server_host=server_host,
        server_port=server_port,
        auto_reconnect=True
    )

    client = Client(
        connection_config=conn_config,
        log_level=Logger.INFO
    )

    # Enable all streams initially
    client.enable_stream(StreamType.MOUSE)
    client.enable_stream(StreamType.KEYBOARD)
    client.enable_stream(StreamType.CLIPBOARD)

    # Start client
    if not await client.start():
        print("Failed to start client")
        return

    print(f"Client started, connecting to {server_host}:{server_port}...")

    try:
        # Wait for connection
        print("Waiting for connection...")
        for i in range(10):
            await asyncio.sleep(0.5)
            if client.is_connected():
                print(f"✓ Connected!")
                break
        else:
            print("✗ Connection timeout")
            return

        print(f"\nInitial state:")
        print(f"  Enabled streams: {client.get_enabled_streams()}")
        print(f"  Active streams: {client.get_active_streams()}\n")
        await asyncio.sleep(3)

        # Disable mouse
        print("Disabling MOUSE stream...")
        await client.disable_stream_runtime(StreamType.MOUSE)
        print(f"  Active streams: {client.get_active_streams()}\n")
        await asyncio.sleep(3)

        # Disable keyboard
        print("Disabling KEYBOARD stream...")
        await client.disable_stream_runtime(StreamType.KEYBOARD)
        print(f"  Active streams: {client.get_active_streams()}\n")
        await asyncio.sleep(3)

        # Re-enable mouse
        print("Re-enabling MOUSE stream...")
        await client.enable_stream_runtime(StreamType.MOUSE)
        print(f"  Active streams: {client.get_active_streams()}\n")
        await asyncio.sleep(3)

        # Re-enable keyboard
        print("Re-enabling KEYBOARD stream...")
        await client.enable_stream_runtime(StreamType.KEYBOARD)
        print(f"  Active streams: {client.get_active_streams()}\n")
        await asyncio.sleep(3)

        print("Demo completed. Client will continue running...")
        print("Press Ctrl+C to stop\n")

        # Keep running
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\nKeyboard interrupt received")
    finally:
        await client.stop()
        print("Client stopped")


async def ssl_setup_demo():
    """Demo for SSL certificate setup workflow"""

    print("\n" + "="*60)
    print("SSL Certificate Setup Demo")
    print("="*60 + "\n")

    server_host = input("Enter server host: ").strip()
    if not server_host:
        print("Server host required")
        return

    server_port = int(input("Enter server port (default: 5555): ").strip() or "5555")
    cert_port = int(input("Enter certificate sharing port (default: 5556): ").strip() or "5556")

    conn_config = ClientConnectionConfig(
        server_host=server_host,
        server_port=server_port,
        auto_reconnect=True
    )

    client = Client(
        connection_config=conn_config,
        log_level=Logger.INFO
    )

    # Enable streams
    client.enable_stream(StreamType.MOUSE)
    client.enable_stream(StreamType.KEYBOARD)
    client.enable_stream(StreamType.CLIPBOARD)

    print("\n" + "="*60)
    print("Step 1: Receive Certificate")
    print("="*60)
    print("\nOn the server, run the command: share ca")
    print("The server will display a 6-digit OTP\n")

    input("Press Enter when server is ready with OTP...")

    otp = input("\nEnter OTP from server: ").strip()

    print("\nReceiving certificate...")
    success = await client.receive_certificate(
        otp=otp,
        server_host=server_host,
        server_port=cert_port
    )

    if not success:
        print("✗ Failed to receive certificate. Aborting.")
        return

    print("✓ Certificate received successfully!")
    print(f"  Certificate saved to: {client.get_certificate_path()}\n")

    print("="*60)
    print("Step 2: Enable SSL")
    print("="*60)

    if client.enable_ssl():
        print("✓ SSL enabled\n")
    else:
        print("✗ Failed to enable SSL\n")
        return

    print("="*60)
    print("Step 3: Connect with SSL")
    print("="*60)

    print(f"\nConnecting to {server_host}:{server_port} with SSL...\n")

    if not await client.start():
        print("✗ Failed to start client")
        return

    print("Client started, waiting for connection...")

    try:
        # Wait for connection
        for i in range(10):
            await asyncio.sleep(0.5)
            if client.is_connected():
                print(f"\n✓ Connected with SSL!")
                print(f"  Active streams: {client.get_active_streams()}")
                break
        else:
            print("\n✗ Connection timeout")
            return

        print("\n" + "="*60)
        print("SSL Setup Complete!")
        print("="*60)
        print("\nClient is now connected securely with SSL/TLS encryption.")
        print("Press Ctrl+C to stop\n")

        # Keep running
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\nKeyboard interrupt received")
    finally:
        await client.stop()
        print("Client stopped")


async def main():
    """Entry point"""
    import sys

    print("\n" + "="*60)
    print("PyContinuity Advanced Client Examples")
    print("="*60)
    print("\nSelect mode:")
    print("1. Interactive mode (default)")
    print("2. Automated demo")
    print("3. SSL setup demo")
    print()

    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        mode = input("Enter mode (1-3, default: 1): ").strip() or "1"

    if mode == "1" or mode == "interactive":
        await interactive_client()
    elif mode == "2" or mode == "demo":
        await automated_demo()
    elif mode == "3" or mode == "ssl":
        await ssl_setup_demo()
    else:
        print(f"Invalid mode: {mode}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
    except RuntimeError as e:
        print(f"\nRuntime error: {e}")

