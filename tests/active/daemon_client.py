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
    """CLI client for daemon interaction"""

    def __init__(self, socket_path: Optional[str] = None):
        self.socket_path = socket_path or Daemon.DEFAULT_SOCKET_PATH

    async def execute_command(
        self, command: str, params: Optional[dict] = None, verbose: bool = False
    ):
        """Execute a command and display response"""
        try:
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
                        print(f"  {key}:")
                        if isinstance(value, dict) and len(value) <= 5:
                            # Small dict, print inline
                            for k, v in value.items():
                                print(f"    {k}: {v}")
                        elif isinstance(value, list) and len(value) <= 10:
                            # Small list
                            for idx, item in enumerate(value):
                                if isinstance(item, dict):
                                    print(f"    [{idx}]:")
                                    for k, v in item.items():
                                        print(f"      {k}: {v}")
                                else:
                                    print(f"    [{idx}] {item}")
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
  # Check daemon status
  %(prog)s status
  
  # Start server
  %(prog)s start-server
  
  # Start client
  %(prog)s start-client
  
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
  
  # Share certificate (server)
  %(prog)s share-cert
  
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

    if not args.command:
        parser.print_help()
        return 1

    # Create client
    client = DaemonClient(socket_path=args.socket)

    # Map CLI commands to daemon commands
    command_map = {
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

    daemon_command = command_map.get(args.command)
    if not daemon_command:
        print(f"Unknown command: {args.command}")
        return 1

    # Build parameters based on command
    params = {}

    if args.command == "set-server-config":
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

        if not params:
            print("Error: No parameters provided. Use --help to see available options.")
            return 1

    elif args.command == "set-client-config":
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

        if not params:
            print("Error: No parameters provided. Use --help to see available options.")
            return 1

    elif args.command in ("save-config", "reload-config"):
        params["type"] = args.type

    elif args.command == "enable-stream":
        params["stream_type"] = args.stream_type
        params["service"] = args.service

    elif args.command == "disable-stream":
        params["stream_type"] = args.stream_type
        params["service"] = args.service

    elif args.command == "get-streams":
        params["service"] = args.service

    elif args.command == "add-client":
        if args.hostname:
            params["hostname"] = args.hostname
        if args.ip_address:
            params["ip_address"] = args.ip_address
        params["screen_position"] = args.screen_position

        if not args.hostname and not args.ip_address:
            print("Error: Must provide either --hostname or --ip-address")
            return 1

    elif args.command == "remove-client":
        if args.hostname:
            params["hostname"] = args.hostname
        if args.ip_address:
            params["ip_address"] = args.ip_address

        if not args.hostname and not args.ip_address:
            print("Error: Must provide either --hostname or --ip-address")
            return 1

    elif args.command == "edit-client":
        if args.hostname:
            params["hostname"] = args.hostname
        if args.ip_address:
            params["ip_address"] = args.ip_address
        params["new_screen_position"] = args.new_screen_position

        if not args.hostname and not args.ip_address:
            print("Error: Must provide either --hostname or --ip-address")
            return 1

    elif args.command in ("enable-ssl", "disable-ssl"):
        params["service"] = args.service

    elif args.command == "share-cert":
        if hasattr(args, "host") and args.host:
            params["host"] = args.host

    elif args.command in ("receive-cert", "set-otp"):
        params["otp"] = args.otp

    elif args.command == "choose-server":
        params["uid"] = args.uid

    elif args.command == "discover":
        params["timeout"] = args.timeout

    # Execute command
    success = await client.execute_command(daemon_command, params, verbose=args.verbose)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
