#!/usr/bin/env python3
"""
Command-line client for interacting with pyContinuity daemon.

This module provides a CLI interface to send commands to the daemon
and display responses in a user-friendly format.
"""

import asyncio
import argparse
import json
import sys
from typing import Optional

try:
    from service.daemon import send_daemon_command, DaemonCommand, Daemon
except ImportError:
    # Fallback for when running as standalone script
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from service.daemon import send_daemon_command, DaemonCommand, Daemon


class DaemonClient:
    """CLI client for daemon interaction with persistent connection"""

    def __init__(self, socket_path: Optional[str] = None):
        self._buffer = None
        self.socket_path = socket_path or Daemon.DEFAULT_SOCKET_PATH
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected = False
        self.listener_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._connection_lock = asyncio.Lock()
        self._welcome_received = asyncio.Event()
        self._welcome_message: Optional[dict] = None
        self._command_parser = self._create_command_parser()

    @staticmethod
    def _create_command_parser() -> argparse.ArgumentParser:
        """Create argument parser for commands (reused in CLI and interactive mode)"""
        parser = argparse.ArgumentParser(
            prog="",
            description="pyContinuity Daemon Commands",
            add_help=False,  # We'll handle help ourselves
        )

        subparsers = parser.add_subparsers(dest="command", help="Command to execute")

        # Service control commands
        server_choice_parser = subparsers.add_parser(
            "service-choice", help="Choose service to enable (server or client)"
        )
        server_choice_parser.add_argument(
            "--service",
            "-s",
            choices=["server", "client"],
            required=True,
            help="Service to enable",
        )

        subparsers.add_parser("start-server", help="Start server service")
        subparsers.add_parser("stop-server", help="Stop server service")
        subparsers.add_parser("start-client", help="Start client service")
        subparsers.add_parser("stop-client", help="Stop client service")

        # Status commands
        subparsers.add_parser("status", help="Get overall daemon status")
        subparsers.add_parser("server-status", help="Get server status")
        subparsers.add_parser("client-status", help="Get client status")

        # Configuration commands
        subparsers.add_parser("get-server-config", help="Get server configuration")

        set_server_parser = subparsers.add_parser(
            "set-server-config", help="Set server configuration"
        )
        set_server_parser.add_argument("--host", help="Server host address")
        set_server_parser.add_argument("--port", type=int, help="Server port")
        set_server_parser.add_argument(
            "--heartbeat-interval", type=int, help="Heartbeat interval"
        )
        set_server_parser.add_argument(
            "--ssl-enabled", action="store_true", help="Enable SSL"
        )
        set_server_parser.add_argument(
            "--no-ssl", dest="ssl_enabled", action="store_false", help="Disable SSL"
        )
        set_server_parser.add_argument("--log-level", type=int, help="Log level")

        subparsers.add_parser("get-client-config", help="Get client configuration")

        set_client_parser = subparsers.add_parser(
            "set-client-config", help="Set client configuration"
        )
        set_client_parser.add_argument(
            "--server-host", help="Server host to connect to"
        )
        set_client_parser.add_argument(
            "--server-port", type=int, help="Server port to connect to"
        )
        set_client_parser.add_argument("--hostname", help="Client hostname")
        set_client_parser.add_argument(
            "--heartbeat-interval", type=int, help="Heartbeat interval"
        )
        set_client_parser.add_argument(
            "--auto-reconnect", action="store_true", help="Enable auto-reconnect"
        )
        set_client_parser.add_argument(
            "--no-auto-reconnect",
            dest="auto_reconnect",
            action="store_false",
            help="Disable auto-reconnect",
        )
        set_client_parser.add_argument(
            "--ssl-enabled", action="store_true", help="Enable SSL"
        )
        set_client_parser.add_argument(
            "--no-ssl", dest="ssl_enabled", action="store_false", help="Disable SSL"
        )
        set_client_parser.add_argument("--log-level", type=int, help="Log level")

        save_config_parser = subparsers.add_parser(
            "save-config", help="Save configuration to disk"
        )
        save_config_parser.add_argument(
            "--type",
            choices=["server", "client", "both"],
            default="both",
            help="Which configuration to save",
        )

        reload_config_parser = subparsers.add_parser(
            "reload-config", help="Reload configuration from disk"
        )
        reload_config_parser.add_argument(
            "--type",
            choices=["server", "client", "both"],
            default="both",
            help="Which configuration to reload",
        )

        # Stream management
        enable_stream_parser = subparsers.add_parser(
            "enable-stream", help="Enable a stream"
        )
        enable_stream_parser.add_argument(
            "--stream-type",
            "-t",
            required=True,
            type=int,
            help="Stream type (1=MOUSE, 4=KEYBOARD, 12=CLIPBOARD)",
        )
        enable_stream_parser.add_argument(
            "--service",
            "-s",
            choices=["server", "client", "auto"],
            default="auto",
            help="Which service to enable stream on",
        )

        disable_stream_parser = subparsers.add_parser(
            "disable-stream", help="Disable a stream"
        )
        disable_stream_parser.add_argument(
            "--stream-type",
            "-t",
            required=True,
            type=int,
            help="Stream type (1=MOUSE, 2=KEYBOARD, 3=CLIPBOARD)",
        )
        disable_stream_parser.add_argument(
            "--service",
            "-s",
            choices=["server", "client", "auto"],
            default="auto",
            help="Which service to disable stream on",
        )

        get_streams_parser = subparsers.add_parser(
            "get-streams", help="Get stream information"
        )
        get_streams_parser.add_argument(
            "--service",
            "-s",
            choices=["server", "client", "auto"],
            default="auto",
            help="Which service to query",
        )

        # Client management (server only)
        subparsers.add_parser(
            "list-clients", help="List registered clients (server only)"
        )

        add_client_parser = subparsers.add_parser(
            "add-client", help="Add a client (server only)"
        )
        add_client_parser.add_argument("--hostname", help="Client hostname")
        add_client_parser.add_argument("--ip-address", help="Client IP address")
        add_client_parser.add_argument(
            "--screen-position",
            required=True,
            choices=["top", "bottom", "left", "right"],
            help="Screen position",
        )

        remove_client_parser = subparsers.add_parser(
            "remove-client", help="Remove a client (server only)"
        )
        remove_client_parser.add_argument("--hostname", help="Client hostname")
        remove_client_parser.add_argument("--ip-address", help="Client IP address")

        edit_client_parser = subparsers.add_parser(
            "edit-client", help="Edit a client (server only)"
        )
        edit_client_parser.add_argument("--hostname", help="Client hostname")
        edit_client_parser.add_argument("--ip-address", help="Client IP address")
        edit_client_parser.add_argument(
            "--new-screen-position",
            required=True,
            choices=["top", "bottom", "left", "right"],
            help="New screen position",
        )

        # SSL/Certificate management
        enable_ssl_parser = subparsers.add_parser("enable-ssl", help="Enable SSL")
        enable_ssl_parser.add_argument(
            "--service",
            "-s",
            choices=["server", "client", "auto"],
            default="auto",
            help="Which service to enable SSL on",
        )

        disable_ssl_parser = subparsers.add_parser("disable-ssl", help="Disable SSL")
        disable_ssl_parser.add_argument(
            "--service",
            "-s",
            choices=["server", "client", "auto"],
            default="auto",
            help="Which service to disable SSL on",
        )

        share_cert_parser = subparsers.add_parser(
            "share-cert", help="Share certificate (server only)"
        )
        share_cert_parser.add_argument("--host", help="Host address for sharing")

        receive_cert_parser = subparsers.add_parser(
            "receive-cert", help="Receive certificate (client only)"
        )
        receive_cert_parser.add_argument(
            "--otp", required=True, help="6-digit OTP from server"
        )

        # Server selection (client only)
        subparsers.add_parser(
            "check-server-choice", help="Check if server choice is needed (client)"
        )
        subparsers.add_parser(
            "get-found-servers", help="Get list of found servers (client)"
        )

        choose_server_parser = subparsers.add_parser(
            "choose-server", help="Choose a server (client)"
        )
        choose_server_parser.add_argument(
            "--uid", required=True, help="UID of the server to choose"
        )

        subparsers.add_parser("check-otp", help="Check if OTP is needed (client)")

        set_otp_parser = subparsers.add_parser(
            "set-otp", help="Set OTP for certificate (client)"
        )
        set_otp_parser.add_argument(
            "--otp", required=True, help="6-digit OTP from server"
        )

        # Service discovery
        discover_parser = subparsers.add_parser(
            "discover", help="Discover services on network"
        )
        discover_parser.add_argument(
            "--timeout", type=int, default=5, help="Discovery timeout in seconds"
        )

        # Daemon control
        subparsers.add_parser("shutdown", help="Shutdown the daemon")
        subparsers.add_parser("ping", help="Ping the daemon")

        return parser

    @staticmethod
    def _build_params_from_args(args) -> dict:
        """Build command parameters from parsed arguments"""
        params = {}
        command = args.command

        if command == "service-choice":
            if args.service:
                params["service"] = args.service
        elif command == "set-server-config":
            if args.host:
                params["host"] = args.host
            if args.port:
                params["port"] = args.port
            if args.heartbeat_interval:
                params["heartbeat_interval"] = args.heartbeat_interval
            if hasattr(args, "ssl_enabled") and args.ssl_enabled is not None:
                params["ssl_enabled"] = args.ssl_enabled
            if args.log_level is not None:
                params["log_level"] = args.log_level

        elif command == "set-client-config":
            if args.server_host:
                params["server_host"] = args.server_host
            if args.server_port:
                params["server_port"] = args.server_port
            if args.hostname:
                params["hostname"] = args.hostname
            if args.heartbeat_interval:
                params["heartbeat_interval"] = args.heartbeat_interval
            if hasattr(args, "auto_reconnect") and args.auto_reconnect is not None:
                params["auto_reconnect"] = args.auto_reconnect
            if hasattr(args, "ssl_enabled") and args.ssl_enabled is not None:
                params["ssl_enabled"] = args.ssl_enabled
            if args.log_level is not None:
                params["log_level"] = args.log_level

        elif command in ("save-config", "reload-config"):
            params["type"] = args.type

        elif command == "enable-stream":
            params["stream_type"] = args.stream_type
            params["service"] = args.service

        elif command == "disable-stream":
            params["stream_type"] = args.stream_type
            params["service"] = args.service

        elif command == "get-streams":
            params["service"] = args.service

        elif command == "add-client":
            if args.hostname:
                params["hostname"] = args.hostname
            if args.ip_address:
                params["ip_address"] = args.ip_address
            params["screen_position"] = args.screen_position

        elif command == "remove-client":
            if args.hostname:
                params["hostname"] = args.hostname
            if args.ip_address:
                params["ip_address"] = args.ip_address

        elif command == "edit-client":
            if args.hostname:
                params["hostname"] = args.hostname
            if args.ip_address:
                params["ip_address"] = args.ip_address
            params["new_screen_position"] = args.new_screen_position

        elif command in ("enable-ssl", "disable-ssl"):
            params["service"] = args.service

        elif command == "share-cert":
            if hasattr(args, "host") and args.host:
                params["host"] = args.host

        elif command in ("receive-cert", "set-otp"):
            params["otp"] = args.otp

        elif command == "choose-server":
            params["uid"] = args.uid

        elif command == "discover":
            params["timeout"] = args.timeout

        return params

    @staticmethod
    def _get_daemon_command(command: str) -> str:
        """Map CLI command name to DaemonCommand value"""
        command_map = {
            "service-choice": DaemonCommand.SERVICE_CHOICE,
            "start-server": DaemonCommand.START_SERVER,
            "stop-server": DaemonCommand.STOP_SERVER,
            "start-client": DaemonCommand.START_CLIENT,
            "stop-client": DaemonCommand.STOP_CLIENT,
            "status": DaemonCommand.STATUS,
            "server-status": DaemonCommand.SERVER_STATUS,
            "client-status": DaemonCommand.CLIENT_STATUS,
            "get-server-config": DaemonCommand.GET_SERVER_CONFIG,
            "set-server-config": DaemonCommand.SET_SERVER_CONFIG,
            "get-client-config": DaemonCommand.GET_CLIENT_CONFIG,
            "set-client-config": DaemonCommand.SET_CLIENT_CONFIG,
            "save-config": DaemonCommand.SAVE_CONFIG,
            "reload-config": DaemonCommand.RELOAD_CONFIG,
            "enable-stream": DaemonCommand.ENABLE_STREAM,
            "disable-stream": DaemonCommand.DISABLE_STREAM,
            "get-streams": DaemonCommand.GET_STREAMS,
            "add-client": DaemonCommand.ADD_CLIENT,
            "remove-client": DaemonCommand.REMOVE_CLIENT,
            "edit-client": DaemonCommand.EDIT_CLIENT,
            "list-clients": DaemonCommand.LIST_CLIENTS,
            "enable-ssl": DaemonCommand.ENABLE_SSL,
            "disable-ssl": DaemonCommand.DISABLE_SSL,
            "share-cert": DaemonCommand.SHARE_CERTIFICATE,
            "receive-cert": DaemonCommand.RECEIVE_CERTIFICATE,
            "check-server-choice": DaemonCommand.CHECK_SERVER_CHOICE_NEEDED,
            "get-found-servers": DaemonCommand.GET_FOUND_SERVERS,
            "choose-server": DaemonCommand.CHOOSE_SERVER,
            "check-otp": DaemonCommand.CHECK_OTP_NEEDED,
            "set-otp": DaemonCommand.SET_OTP,
            "discover": DaemonCommand.DISCOVER_SERVICES,
            "shutdown": DaemonCommand.SHUTDOWN,
            "ping": DaemonCommand.PING,
        }
        return command_map.get(command, command)

    async def connect(self) -> bool:
        """
        Establish persistent connection to daemon.

        Returns:
            True if connected successfully, False otherwise
        """
        if self.connected:
            return True

        try:
            import platform

            IS_WINDOWS = platform.system() == "Windows"
            socket_path = self.socket_path

            if IS_WINDOWS or ":" in socket_path:
                # TCP connection
                if ":" in socket_path:
                    host, port_str = socket_path.split(":", 1)
                    port = int(port_str)
                else:
                    host, port = "127.0.0.1", 37492

                self.reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=5.0
                )
            else:
                # Unix socket
                import os

                if not os.path.exists(socket_path):
                    raise ConnectionError(
                        f"Daemon not running (socket not found: {socket_path})"
                    )

                self.reader, self.writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(socket_path), timeout=5.0
                )

            self.connected = True

            # Start listener task (it will handle the welcome message)
            self.listener_task = asyncio.create_task(self._listen_for_events())

            # Wait for welcome message from listener
            try:
                await asyncio.wait_for(self._welcome_received.wait(), timeout=5.0)
                if self._welcome_message and self._welcome_message.get("success"):
                    print("✓ Connected to daemon")
                    if self._welcome_message.get("data", {}).get("version"):
                        print(f"  Version: {self._welcome_message['data']['version']}")
            except asyncio.TimeoutError:
                print("⚠ Warning: No welcome message received")

            return True

        except (ConnectionRefusedError, OSError) as e:
            print(f"✗ Connection Error: Cannot connect to daemon: {e}")
            print(f"   Make sure the daemon is running on: {self.socket_path}")
            return False
        except asyncio.TimeoutError:
            print("✗ Connection Error: Timeout connecting to daemon")
            return False
        except Exception as e:
            print(f"✗ Error: {e}")
            return False

    async def disconnect(self):
        """Close connection to daemon"""
        if not self.connected:
            return

        self._stop_event.set()

        # Stop listener task
        if self.listener_task and not self.listener_task.done():
            self.listener_task.cancel()
            try:
                await self.listener_task
            except asyncio.CancelledError:
                pass

        # Close connection
        async with self._connection_lock:
            if self.writer:
                try:
                    self.writer.close()
                    await self.writer.wait_closed()
                except Exception:
                    pass
                self.writer = None
                self.reader = None

        self.connected = False

        # Reset welcome state for potential reconnection
        self._welcome_received.clear()
        self._welcome_message = None
        self._stop_event.clear()

        self._buffer = bytearray()

        print("\n✓ Disconnected from daemon")

    async def _listen_for_events(self):
        """
        Background task to listen for events/notifications from daemon.
        This runs continuously until disconnection.
        """
        first_message = True
        self._buffer = bytearray()
        try:
            while not self._stop_event.is_set() and self.connected:
                if not self.reader:
                    break

                try:
                    # Read data from daemon
                    chunk = await asyncio.wait_for(self.reader.read(65536), timeout=1.0)

                    if not chunk:
                        print("\n⚠ Connection closed by daemon")
                        break

                    self._buffer.extend(chunk)

                    # Process complete messages: message_bytes + 4-byte length + '\n'
                    while True:
                        # Check if we have at least the newline delimiter
                        try:
                            newline_idx = self._buffer.index(b"\n")
                        except ValueError:
                            # No complete message yet
                            break

                        # Extract everything before the newline
                        complete_part = self._buffer[:newline_idx]

                        # Check if we have at least 4 bytes for the length marker
                        if len(complete_part) < 4:
                            print("\n⚠ Malformed message: too short")
                            self._buffer = self._buffer[newline_idx + 1 :]
                            continue

                        # Extract length marker (last 4 bytes before newline)
                        length_bytes = complete_part[-4:]
                        message_length = int.from_bytes(length_bytes, byteorder="big")

                        # Extract message bytes (everything except the last 4 bytes)
                        message_bytes = complete_part[:-4]

                        # Verify length matches
                        if len(message_bytes) != message_length:
                            print(
                                f"\n⚠ Length mismatch: expected {message_length}, got {len(message_bytes)}"
                            )
                            self._buffer = self._buffer[newline_idx + 1 :]
                            continue

                        # Remove processed message from buffer
                        self._buffer = self._buffer[newline_idx + 1 :]

                        # Decode and parse JSON
                        try:
                            message_str = message_bytes.decode("utf-8")
                            response = json.loads(message_str)

                            # First message is the welcome message
                            if first_message:
                                first_message = False
                                self._welcome_message = response
                                self._welcome_received.set()
                                continue

                            await self._handle_daemon_message(response)

                        except json.JSONDecodeError as e:
                            print(f"\n⚠ Invalid JSON received: {e}")
                            continue
                        except UnicodeDecodeError as e:
                            print(f"\n⚠ Invalid UTF-8 encoding: {e}")
                            continue

                except asyncio.TimeoutError:
                    # Normal timeout, just continue listening
                    continue

        except asyncio.CancelledError:
            # Task cancelled, normal shutdown
            pass
        except Exception as e:
            print(f"\n✗ Listener error: {e}")
        finally:
            self.connected = False

    async def _handle_daemon_message(self, message: dict):
        """
        Handle messages received from daemon.

        Args:
            message: Parsed JSON message from daemon
        """
        # Check if it's an event notification or command response
        data = message.get("data", {})
        error = message.get("error")
        if error:
            print(f"\n✗ Error from daemon: {error}")
            return
        elif not data:
            print("\n⚠ Empty message received from daemon")
            return

        event_type = message.get("event_type")

        if event_type:
            # It's an event notification
            print(f"\n📢 Event: {event_type}")
            print("   Data:")
            self._print_data(
                {k: v for k, v in data.items() if k not in ["event", "message"]}
            )
        else:
            # It's a command response
            if message.get("success"):
                print("\n✓ Response received")
                if data:
                    self._print_data(data)
            else:
                print(f"\n✗ Error: {message.get('error', 'Unknown error')}")

    async def send_command(
        self, command: str, params: Optional[dict] = None, verbose: bool = False
    ) -> bool:
        """
        Send a command to daemon through the persistent connection.

        Args:
            command: Command to send
            params: Command parameters
            verbose: Enable verbose output

        Returns:
            True if command sent successfully, False otherwise
        """
        if not self.connected or not self.writer:
            print("✗ Not connected to daemon")
            return False

        try:
            if verbose:
                print(f"Sending command: {command}")
                if params:
                    print(f"Parameters: {json.dumps(params, indent=2)}")

            command_data = {"command": command, "params": params or {}}
            print(command_data)
            async with self._connection_lock:
                self.writer.write(Daemon.prepare_msg_bytes(command_data))
                await self.writer.drain()

            if verbose:
                print("✓ Command sent")

            return True

        except Exception as e:
            print(f"✗ Error sending command: {e}")
            if verbose:
                import traceback

                traceback.print_exc()
            return False

    async def interactive_mode(self):
        """
        Interactive mode: allows user to send multiple commands without restarting.
        Combines command input with event listening.
        Uses argparse for command parsing.
        """
        if not self.connected:
            print("✗ Not connected. Call connect() first.")
            return

        print("\n" + "=" * 60)
        print("  Interactive Mode - pyContinuity Daemon Client")
        print("=" * 60)
        print("\nType commands to send to daemon.")
        print("Examples: status, start-server, ping, help, exit")
        print("For command help: <command> --help")
        print("Type 'help' for full command list or 'exit' to quit.\n")

        # Run input loop in executor to not block asyncio
        import concurrent.futures
        import shlex

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        try:
            while self.connected:
                # Get input in thread pool to avoid blocking
                try:
                    loop = asyncio.get_event_loop()
                    user_input = await loop.run_in_executor(executor, input, "daemon> ")

                    user_input = user_input.strip()

                    if not user_input:
                        continue

                    # Handle special commands
                    if user_input.lower() in ("exit", "quit", "q"):
                        print("Exiting interactive mode...")
                        break

                    if user_input.lower() == "help":
                        self._print_interactive_help()
                        continue

                    if user_input.lower() == "clear":
                        import os

                        os.system("clear" if os.name != "nt" else "cls")
                        continue

                    # Parse command using argparse
                    try:
                        # Split command line properly (handles quotes, etc.)
                        args_list = shlex.split(user_input)

                        # Parse with command parser
                        args = self._command_parser.parse_args(args_list)

                        if not args.command:
                            print("✗ No command specified")
                            continue

                        # Get daemon command
                        daemon_cmd = self._get_daemon_command(args.command)

                        # Build parameters
                        params = self._build_params_from_args(args)

                        # Send command
                        await self.send_command(daemon_cmd, params)

                        # Small delay to let response arrive
                        await asyncio.sleep(0.1)

                    except SystemExit:
                        # argparse calls sys.exit on error or --help
                        # We catch it to prevent exiting interactive mode
                        continue
                    except ValueError as e:
                        print(f"✗ Invalid command syntax: {e}")
                        continue
                    except Exception as e:
                        print(f"✗ Error parsing command: {e}")
                        continue

                except EOFError:
                    print("\n\nEOF received, exiting...")
                    break
                except KeyboardInterrupt:
                    print("\n\n⚠ Use 'exit' command to quit interactive mode")
                    continue

        finally:
            executor.shutdown(wait=False)
            print("\n✓ Interactive mode ended")

    def _print_interactive_help(self):
        """Print help for interactive mode using argparse"""
        print("\n" + "=" * 60)
        print("  Interactive Mode Help")
        print("=" * 60)
        print("\nSpecial Commands:")
        print("  help             - Show this help message")
        print("  clear            - Clear the screen")
        print("  exit, quit, q    - Exit interactive mode")
        print("\nAvailable Daemon Commands:")
        print("-" * 60)

        # Use argparse to print help
        import io
        import sys

        # Capture the help output
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()

        try:
            self._command_parser.print_help()
            help_text = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        print(help_text)
        print("\nTips:")
        print("  - Use '<command> --help' for detailed command help")
        print("  - Example: 'discover --timeout 10'")
        print("  - Example: 'add-client --hostname MyPC --screen-position left'")
        print("=" * 60 + "\n")

    async def execute_command(
        self,
        command: str,
        params: Optional[dict] = None,
        verbose: bool = False,
        expect_multiple_responses: bool = False,
        max_responses: int = 2,
        response_timeout: float = 1.0,
    ):
        """
        Execute a command (legacy method for backward compatibility).
        Uses persistent connection if available, otherwise creates temporary connection.

        Args:
            command: Command to send
            params: Command parameters
            verbose: Enable verbose output
            expect_multiple_responses: (Ignored, kept for compatibility)
            max_responses: (Ignored, kept for compatibility)
            response_timeout: (Ignored, kept for compatibility)
        """
        try:
            if self.connected:
                # Use persistent connection
                return await self.send_command(command, params, verbose)
            else:
                # Fallback to single-shot command
                if verbose:
                    print(f"Sending command: {command}")
                    if params:
                        print(f"Parameters: {json.dumps(params, indent=2)}")

                response = await send_daemon_command(
                    command=command, params=params, socket_path=self.socket_path
                )

                if response.get("success"):
                    print("✓ Success")
                    if response.get("data"):
                        self._print_data(response["data"])
                else:
                    print("✗ Error:", response.get("error", "Unknown error"))
                    return False

                return True

        except ConnectionError as e:
            print(f"✗ Connection Error: {e}")
            print(f"   Make sure the daemon is running on: {self.socket_path}")
            return False
        except TimeoutError:
            print("✗ Timeout: Command took too long to execute")
            return False
        except Exception as e:
            print(f"✗ Error: {e}")
            if verbose:
                import traceback

                traceback.print_exc()
            return False

    def _print_data(self, data):
        """Pretty print response data"""
        if isinstance(data, dict):
            # Check if it's a simple message
            if len(data) == 1 and "message" in data:
                print(f"  {data['message']}")
            else:
                for key, value in data.items():
                    if isinstance(value, (dict, list)):
                        print(f"\t\t\t{key}:")
                        if isinstance(value, dict) and len(value) <= 5:
                            # Small dict, print inline
                            for k, v in value.items():
                                print(f"\t\t\t{k}: {v}")
                        elif isinstance(value, list) and len(value) <= 10:
                            # Small list
                            for idx, item in enumerate(value):
                                if isinstance(item, dict):
                                    print(f"\t\t\t[{idx}]:")
                                    for k, v in item.items():
                                        print(f"\t\t\t\t{k}: {v}")
                                else:
                                    print(f"\t\t\t\t[{idx}] {item}")
                        else:
                            # Large structure, use JSON
                            print(json.dumps(value, indent=4))
                    else:
                        print(f"  {key}: {value}")
        elif isinstance(data, list):
            if len(data) == 0:
                print("  (empty list)")
            else:
                print(json.dumps(data, indent=2))
        else:
            print(f"  {data}")


async def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="pyContinuity Daemon Client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check daemon status (single command)
  %(prog)s status
  
  # Start server
  %(prog)s start-server
  
  # Interactive mode - send multiple commands without restarting
  %(prog)s -i
  %(prog)s --interactive
  
  # Interactive mode with initial command
  %(prog)s start-client --interactive
  
  # Persistent mode - listen for daemon events
  %(prog)s start-client --persistent
  
  # Check if server choice needed (client)
  %(prog)s check-server-choice
  
  # Get found servers (client)
  %(prog)s get-found-servers
  
  # Choose server (client)
  %(prog)s choose-server --uid abc123
  
  # Check if OTP needed (client)
  %(prog)s check-otp
  
  # Set OTP (client)
  %(prog)s set-otp --otp 123456
  
  # Share certificate (server) with interactive mode
  %(prog)s share-cert -i
  
  # Discover services
  %(prog)s discover --timeout 5
        """,
    )

    parser.add_argument(
        "--socket",
        default=Daemon.DEFAULT_SOCKET_PATH,
        help="Socket path for daemon communication",
    )

    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    parser.add_argument(
        "--persistent",
        "-p",
        action="store_true",
        help="Keep connection open to listen for daemon events (Ctrl+C to exit)",
    )

    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Interactive mode: keep connection open and allow sending multiple commands",
    )

    parser.add_argument(
        "--multi-response",
        action="store_true",
        help="(Deprecated) Use --persistent instead",
    )

    parser.add_argument(
        "--max-responses",
        type=int,
        default=10,
        help="Maximum number of responses to wait for (default: 10)",
    )

    parser.add_argument(
        "--response-timeout",
        type=float,
        default=1.0,
        help="Timeout in seconds for each response (default: 30.0)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Service control commands
    subparsers.add_parser("start-server", help="Start server service")
    subparsers.add_parser("stop-server", help="Stop server service")
    subparsers.add_parser("start-client", help="Start client service")
    subparsers.add_parser("stop-client", help="Stop client service")

    # Status commands
    subparsers.add_parser("status", help="Get overall daemon status")
    subparsers.add_parser("server-status", help="Get server status")
    subparsers.add_parser("client-status", help="Get client status")

    # Configuration commands
    subparsers.add_parser("get-server-config", help="Get server configuration")

    set_server_parser = subparsers.add_parser(
        "set-server-config", help="Set server configuration"
    )
    set_server_parser.add_argument("--host", help="Server host address")
    set_server_parser.add_argument("--port", type=int, help="Server port")
    set_server_parser.add_argument(
        "--heartbeat-interval", type=int, help="Heartbeat interval"
    )
    set_server_parser.add_argument(
        "--ssl-enabled", action="store_true", help="Enable SSL"
    )
    set_server_parser.add_argument(
        "--no-ssl", dest="ssl_enabled", action="store_false", help="Disable SSL"
    )
    set_server_parser.add_argument("--log-level", type=int, help="Log level")

    subparsers.add_parser("get-client-config", help="Get client configuration")

    set_client_parser = subparsers.add_parser(
        "set-client-config", help="Set client configuration"
    )
    set_client_parser.add_argument("--server-host", help="Server host to connect to")
    set_client_parser.add_argument(
        "--server-port", type=int, help="Server port to connect to"
    )
    set_client_parser.add_argument("--hostname", help="Client hostname")
    set_client_parser.add_argument(
        "--heartbeat-interval", type=int, help="Heartbeat interval"
    )
    set_client_parser.add_argument(
        "--auto-reconnect", action="store_true", help="Enable auto-reconnect"
    )
    set_client_parser.add_argument(
        "--no-auto-reconnect",
        dest="auto_reconnect",
        action="store_false",
        help="Disable auto-reconnect",
    )
    set_client_parser.add_argument(
        "--ssl-enabled", action="store_true", help="Enable SSL"
    )
    set_client_parser.add_argument(
        "--no-ssl", dest="ssl_enabled", action="store_false", help="Disable SSL"
    )
    set_client_parser.add_argument("--log-level", type=int, help="Log level")

    save_config_parser = subparsers.add_parser(
        "save-config", help="Save configuration to disk"
    )
    save_config_parser.add_argument(
        "--type",
        choices=["server", "client", "both"],
        default="both",
        help="Which configuration to save",
    )

    reload_config_parser = subparsers.add_parser(
        "reload-config", help="Reload configuration from disk"
    )
    reload_config_parser.add_argument(
        "--type",
        choices=["server", "client", "both"],
        default="both",
        help="Which configuration to reload",
    )

    # Stream management
    enable_stream_parser = subparsers.add_parser(
        "enable-stream", help="Enable a stream"
    )
    enable_stream_parser.add_argument(
        "--stream-type",
        "-t",
        required=True,
        type=int,
        help="Stream type (1=MOUSE, 4=KEYBOARD, 12=CLIPBOARD)",
    )
    enable_stream_parser.add_argument(
        "--service",
        "-s",
        choices=["server", "client", "auto"],
        default="auto",
        help="Which service to enable stream on",
    )

    disable_stream_parser = subparsers.add_parser(
        "disable-stream", help="Disable a stream"
    )
    disable_stream_parser.add_argument(
        "--stream-type",
        "-t",
        required=True,
        type=int,
        help="Stream type (1=MOUSE, 2=KEYBOARD, 3=CLIPBOARD)",
    )
    disable_stream_parser.add_argument(
        "--service",
        "-s",
        choices=["server", "client", "auto"],
        default="auto",
        help="Which service to disable stream on",
    )

    get_streams_parser = subparsers.add_parser(
        "get-streams", help="Get stream information"
    )
    get_streams_parser.add_argument(
        "--service",
        "-s",
        choices=["server", "client", "auto"],
        default="auto",
        help="Which service to query",
    )

    # Client management (server only)
    subparsers.add_parser("list-clients", help="List registered clients (server only)")

    add_client_parser = subparsers.add_parser(
        "add-client", help="Add a client (server only)"
    )
    add_client_parser.add_argument("--hostname", help="Client hostname")
    add_client_parser.add_argument("--ip-address", help="Client IP address")
    add_client_parser.add_argument(
        "--screen-position",
        required=True,
        choices=["top", "bottom", "left", "right"],
        help="Screen position",
    )

    remove_client_parser = subparsers.add_parser(
        "remove-client", help="Remove a client (server only)"
    )
    remove_client_parser.add_argument("--hostname", help="Client hostname")
    remove_client_parser.add_argument("--ip-address", help="Client IP address")

    edit_client_parser = subparsers.add_parser(
        "edit-client", help="Edit a client (server only)"
    )
    edit_client_parser.add_argument("--hostname", help="Client hostname")
    edit_client_parser.add_argument("--ip-address", help="Client IP address")
    edit_client_parser.add_argument(
        "--new-screen-position",
        required=True,
        choices=["top", "bottom", "left", "right"],
        help="New screen position",
    )

    # SSL/Certificate management
    enable_ssl_parser = subparsers.add_parser("enable-ssl", help="Enable SSL")
    enable_ssl_parser.add_argument(
        "--service",
        "-s",
        choices=["server", "client", "auto"],
        default="auto",
        help="Which service to enable SSL on",
    )

    disable_ssl_parser = subparsers.add_parser("disable-ssl", help="Disable SSL")
    disable_ssl_parser.add_argument(
        "--service",
        "-s",
        choices=["server", "client", "auto"],
        default="auto",
        help="Which service to disable SSL on",
    )

    share_cert_parser = subparsers.add_parser(
        "share-cert", help="Share certificate (server only)"
    )
    share_cert_parser.add_argument("--host", help="Host address for sharing")

    receive_cert_parser = subparsers.add_parser(
        "receive-cert", help="Receive certificate (client only)"
    )
    receive_cert_parser.add_argument(
        "--otp", required=True, help="6-digit OTP from server"
    )

    # Server selection (client only)
    subparsers.add_parser(
        "check-server-choice", help="Check if server choice is needed (client)"
    )
    subparsers.add_parser(
        "get-found-servers", help="Get list of found servers (client)"
    )

    choose_server_parser = subparsers.add_parser(
        "choose-server", help="Choose a server (client)"
    )
    choose_server_parser.add_argument(
        "--uid", required=True, help="UID of the server to choose"
    )

    subparsers.add_parser("check-otp", help="Check if OTP is needed (client)")

    set_otp_parser = subparsers.add_parser(
        "set-otp", help="Set OTP for certificate (client)"
    )
    set_otp_parser.add_argument("--otp", required=True, help="6-digit OTP from server")

    # Service discovery
    discover_parser = subparsers.add_parser(
        "discover", help="Discover services on network"
    )
    discover_parser.add_argument(
        "--timeout", type=int, default=5, help="Discovery timeout in seconds"
    )

    # Daemon control
    subparsers.add_parser("shutdown", help="Shutdown the daemon")
    subparsers.add_parser("ping", help="Ping the daemon")

    args = parser.parse_args()

    # Create client
    client = DaemonClient(socket_path=args.socket)

    # Allow interactive mode without specifying a command
    if args.interactive and not args.command:
        try:
            if not await client.connect():
                return 1
            await client.interactive_mode()
            await client.disconnect()
            return 0
        except KeyboardInterrupt:
            print("\n\n⚠ Interrupted by user")
            if client.connected:
                await client.disconnect()
            return 130

    if not args.command:
        parser.print_help()
        return 1

    # Map CLI commands to daemon commands and build parameters
    daemon_command = DaemonClient._get_daemon_command(args.command)
    params = DaemonClient._build_params_from_args(args)

    # Determine if we need persistent connection
    use_persistent = args.persistent or args.multi_response
    use_interactive = args.interactive

    try:
        if use_interactive:
            # Interactive mode: connect and enter REPL loop
            if not await client.connect():
                return 1

            # If a command was specified, execute it first
            if args.command:
                await client.send_command(daemon_command, params, verbose=args.verbose)
                await asyncio.sleep(0.2)  # Let response arrive

            # Enter interactive mode
            await client.interactive_mode()
            await client.disconnect()
            return 0

        elif use_persistent:
            # Use persistent connection mode (listen only)
            if not await client.connect():
                return 1

            # Send command
            success = await client.send_command(
                daemon_command, params, verbose=args.verbose
            )

            if not success:
                await client.disconnect()
                return 1

            # Keep listening for events
            print("\n👂 Listening for events from daemon (Press Ctrl+C to exit)...")
            try:
                # Wait indefinitely for events
                while client.connected:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                print("\n\n⚠ Interrupted by user")
            finally:
                await client.disconnect()

            return 0

        else:
            # Single-shot command mode
            success = await client.execute_command(
                daemon_command, params, verbose=args.verbose
            )

            return 0 if success else 1

    except KeyboardInterrupt:
        print("\n\n⚠ Interrupted by user")
        if client.connected:
            await client.disconnect()
        return 130  # Standard exit code for SIGINT


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
