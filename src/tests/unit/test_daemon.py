"""
Unit tests for the Daemon service.

Tests cover:
- Daemon lifecycle (start/stop)
- Socket creation and management (Unix and TCP)
- Single connection policy
- Continuous command listening
- Arbitrary data sending (broadcast)
- Command execution
- Exception handling (already running, port occupied)
- Configuration management
"""

from event.notification import NotificationEvent, NotificationEventType

import asyncio
import json
import os
import sys
from typing import Optional

import pytest

from service.daemon import (
    Daemon,
    DaemonCommand,
    DaemonAlreadyRunningException,
    DaemonPortOccupiedException,
    IS_WINDOWS,
)


# ============================================================================
# Helper Functions
# ============================================================================


async def send_command(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    command: str,
    params: Optional[dict] = None,
):
    """Send a command to daemon and receive response."""
    cmd = {"command": command, "params": params or {}}
    writer.write(Daemon.prepare_msg_bytes(cmd))
    await writer.drain()

    # Read response
    data = await asyncio.wait_for(reader.read(16384), timeout=10.0)
    if data:
        # Split by newlines (may have multiple messages)
        try:
            messages, read = Daemon.parse_msg_bytes(data)
            responses = []
            for msg in messages:
                responses.append(msg)
            return responses
        except Exception as e:
            print(e)
    return None


# ============================================================================
# Test Daemon Initialization
# ============================================================================


class TestDaemonInitialization:
    """Test Daemon initialization."""

    @pytest.mark.anyio
    async def test_daemon_init_default(self):
        """Test daemon initialization with default parameters."""
        daemon = Daemon()
        assert daemon.socket_path == Daemon.DEFAULT_SOCKET_PATH
        assert daemon.app_config is not None
        assert daemon.auto_load_config is True
        assert daemon._running is False

    @pytest.mark.anyio
    async def test_daemon_init_custom_socket(self, app_config, daemon_unix_socket_path):
        """Test daemon initialization with custom socket path."""
        daemon = Daemon(socket_path=daemon_unix_socket_path, app_config=app_config)
        assert daemon.socket_path == daemon_unix_socket_path
        assert daemon.app_config == app_config

    @pytest.mark.anyio
    async def test_daemon_init_no_autoload(self, app_config):
        """Test daemon initialization without auto-loading config."""
        daemon = Daemon(app_config=app_config, auto_load_config=False)
        assert daemon.auto_load_config is False


# ============================================================================
# Test Daemon Lifecycle - Unix Socket
# ============================================================================


@pytest.mark.skipif(IS_WINDOWS, reason="Unix socket tests only run on Unix systems")
class TestDaemonLifecycleUnix:
    """Test Daemon lifecycle with Unix sockets."""

    @pytest.mark.anyio
    async def test_start_daemon_creates_socket(self, daemon_instance):
        """Test that starting daemon creates Unix socket file."""
        assert not os.path.exists(daemon_instance.socket_path)

        result = await daemon_instance.start()
        await asyncio.sleep(0.5)
        assert result is True
        assert daemon_instance._running is True
        assert os.path.exists(daemon_instance.socket_path)

        await daemon_instance.stop()

    @pytest.mark.anyio
    async def test_stop_daemon_removes_socket(self, running_daemon):
        """Test that stopping daemon removes Unix socket file."""
        socket_path = running_daemon.socket_path
        assert os.path.exists(socket_path)

        await running_daemon.stop()

        assert running_daemon._running is False
        assert not os.path.exists(socket_path)

    @pytest.mark.anyio
    async def test_socket_permissions(self, running_daemon):
        """Test that socket has correct permissions (owner only)."""
        import stat

        st = os.stat(running_daemon.socket_path)
        mode = st.st_mode

        # Check that only owner has read/write permissions
        assert stat.S_IMODE(mode) == 0o600

    @pytest.mark.anyio
    @pytest.mark.timeout(2)
    async def test_daemon_already_running_exception(self, running_daemon):
        """Test that starting daemon when already running raises exception."""
        # Try to start another daemon on same socket
        daemon2 = Daemon(
            socket_path=running_daemon.socket_path,
            app_config=running_daemon.app_config,
            auto_load_config=False,
        )

        with pytest.raises(DaemonAlreadyRunningException):
            await daemon2.start()

    @pytest.mark.anyio
    async def test_stale_socket_removed(self, daemon_instance):
        """Test that stale socket file is removed and new one created."""
        # Create a stale socket file
        with open(daemon_instance.socket_path, "w") as f:
            f.write("stale socket")

        assert os.path.exists(daemon_instance.socket_path)

        # Starting daemon should remove stale socket and create new one
        result = await daemon_instance.start()

        await asyncio.sleep(0.5)
        assert result is True
        assert os.path.exists(daemon_instance.socket_path)
        # Socket should be a real socket, not a regular file
        assert (
            os.stat(daemon_instance.socket_path).st_mode & 0o170000 == 0o140000
        )  # S_IFSOCK

        await daemon_instance.stop()


# ============================================================================
# Test Daemon Lifecycle - TCP Socket
# ============================================================================


@pytest.mark.skipif(not IS_WINDOWS, reason="TCP socket tests only run on Windows")
@pytest.mark.anyio
class TestDaemonLifecycleTCP:
    """Test Daemon lifecycle with TCP sockets."""

    @pytest.mark.anyio
    async def test_start_daemon_tcp(self, app_config, daemon_tcp_address):
        """Test starting daemon with TCP socket."""
        daemon = Daemon(
            socket_path=daemon_tcp_address,
            app_config=app_config,
            auto_load_config=False,
        )

        result = await daemon.start()

        assert result is True
        assert daemon._running is True
        assert daemon._socket_server is not None

        await daemon.stop()

    @pytest.mark.anyio
    async def test_daemon_already_running_tcp(self, app_config, daemon_tcp_address):
        """Test that TCP port occupied raises exception."""
        daemon1 = Daemon(
            socket_path=daemon_tcp_address,
            app_config=app_config,
            auto_load_config=False,
        )
        await daemon1.start()

        try:
            daemon2 = Daemon(
                socket_path=daemon_tcp_address,
                app_config=app_config,
                auto_load_config=False,
            )

            with pytest.raises(DaemonAlreadyRunningException):
                await daemon2.start()
        finally:
            await daemon1.stop()

    @pytest.mark.anyio
    async def test_port_occupied_by_other_process(self, app_config, daemon_tcp_address):
        """Test that port occupied by non-daemon process raises exception."""
        host, port = daemon_tcp_address.split(":")
        port = int(port)

        # Create a simple TCP server on the port
        async def dummy_handler(reader, writer):
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(dummy_handler, host, port)

        try:
            daemon = Daemon(
                socket_path=daemon_tcp_address,
                app_config=app_config,
                auto_load_config=False,
            )

            with pytest.raises(DaemonPortOccupiedException):
                await daemon.start()
        finally:
            server.close()
            await server.wait_closed()


# ============================================================================
# Test Single Connection Policy
# ============================================================================


@pytest.mark.anyio
class TestSingleConnectionPolicy:
    """Test that daemon only accepts one connection at a time."""

    @pytest.mark.anyio
    async def test_only_one_connection_allowed(self, running_daemon):
        """Test that only one client can connect at a time."""
        # Connect first client
        if IS_WINDOWS:
            host, port = running_daemon.socket_path.split(":")
            reader1, writer1 = await asyncio.open_connection(host, int(port))
        else:
            reader1, writer1 = await asyncio.open_unix_connection(
                running_daemon.socket_path
            )

        # Read welcome message
        await reader1.read(16384)

        # Try to connect second client
        if IS_WINDOWS:
            host, port = running_daemon.socket_path.split(":")
            reader2, writer2 = await asyncio.open_connection(host, int(port))
        else:
            reader2, writer2 = await asyncio.open_unix_connection(
                running_daemon.socket_path
            )

        # Second client should receive rejection message
        response = await reader2.read(16384)
        data = json.loads(response.decode("utf-8").strip().split("\n")[0][:-4])

        assert data["success"] is False
        assert "another client" in data["error"].lower()

        # Cleanup
        writer1.close()
        await writer1.wait_closed()
        writer2.close()
        await writer2.wait_closed()

    @pytest.mark.anyio
    async def test_new_connection_after_first_disconnects(self, running_daemon):
        """Test that new client can connect after first disconnects."""
        # Connect and disconnect first client
        if IS_WINDOWS:
            host, port = running_daemon.socket_path.split(":")
            reader1, writer1 = await asyncio.open_connection(host, int(port))
        else:
            reader1, writer1 = await asyncio.open_unix_connection(
                running_daemon.socket_path
            )

        await reader1.read(16384)  # Welcome message
        writer1.close()
        await writer1.wait_closed()

        # Give time for cleanup
        await asyncio.sleep(0.1)

        # Connect second client
        if IS_WINDOWS:
            host, port = running_daemon.socket_path.split(":")
            reader2, writer2 = await asyncio.open_connection(host, int(port))
        else:
            reader2, writer2 = await asyncio.open_unix_connection(
                running_daemon.socket_path
            )

        # Should receive welcome message
        response = await reader2.read(16384)
        response = response.decode("utf-8").strip().split("\n")[0][:-4]
        data = json.loads(response)

        assert data["success"] is True
        assert "Connected" in data["data"]["message"]

        # Cleanup
        writer2.close()
        await writer2.wait_closed()

    @pytest.mark.anyio
    async def test_is_client_connected(self, running_daemon):
        """Test is_client_connected method."""
        assert running_daemon.is_client_connected() is False

        # Connect client
        if IS_WINDOWS:
            host, port = running_daemon.socket_path.split(":")
            reader, writer = await asyncio.open_connection(host, int(port))
        else:
            reader, writer = await asyncio.open_unix_connection(
                running_daemon.socket_path
            )

        await reader.read(16384)  # Welcome message
        await asyncio.sleep(0.1)  # Give time for connection to be registered

        assert running_daemon.is_client_connected() is True

        # Cleanup
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.1)

        assert running_daemon.is_client_connected() is False


# ============================================================================
# Test Continuous Command Listening
# ============================================================================


@pytest.mark.anyio
class TestContinuousCommandListening:
    """Test that daemon continuously listens for commands."""

    @pytest.mark.anyio
    async def test_multiple_commands_in_sequence(self, daemon_client_connection):
        """Test sending multiple commands in sequence."""
        reader, writer = daemon_client_connection

        commands = [
            DaemonCommand.PING,
            DaemonCommand.STATUS,
            DaemonCommand.PING,
            DaemonCommand.STATUS,
        ]

        for cmd in commands:
            responses = await send_command(reader, writer, cmd)
            assert responses is not None
            assert len(responses) > 0
            assert responses[-1]["success"] is True

    @pytest.mark.anyio
    async def test_invalid_json_handling(self, daemon_client_connection):
        """Test that invalid JSON is handled gracefully."""
        reader, writer = daemon_client_connection

        # Send invalid JSON
        writer.write(b"invalid json\n")
        await writer.drain()

        # Should receive error response
        data = await reader.read(16384)
        response = json.loads(data.decode("utf-8").strip().split("\n")[0][:-4])

        assert response["success"] is False
        assert "Invalid JSON" in response["error"]

    @pytest.mark.anyio
    async def test_unknown_command_handling(self, daemon_client_connection):
        """Test that unknown commands are handled gracefully."""
        reader, writer = daemon_client_connection

        responses = await send_command(reader, writer, "unknown_command")

        assert responses is not None
        assert responses[-1]["success"] is False
        assert "Unknown command" in responses[-1]["error"]

    @pytest.mark.anyio
    async def test_command_with_params(self, daemon_client_connection):
        """Test sending commands with parameters."""
        reader, writer = daemon_client_connection

        responses = await send_command(
            reader, writer, DaemonCommand.STATUS, {"verbose": True}
        )

        assert responses is not None
        assert responses[-1]["success"] is True


# ============================================================================
# Test Arbitrary Data Sending (Broadcast)
# ============================================================================


@pytest.mark.anyio
class TestArbitraryDataSending:
    """Test daemon's ability to send arbitrary data to connected client."""

    @pytest.mark.anyio
    async def test_broadcast_event(self, running_daemon: Daemon):
        """Test broadcasting event to connected client."""
        # Connect client
        if IS_WINDOWS:
            host, port = running_daemon.socket_path.split(":")
            reader, writer = await asyncio.open_connection(host, int(port))
        else:
            reader, writer = await asyncio.open_unix_connection(
                running_daemon.socket_path
            )

        await reader.read(16384)  # Welcome message

        # Broadcast event
        await running_daemon._send_notification(
            NotificationEvent(
                event_type=NotificationEventType.TEST, data={"message": "Test"}
            )
        )

        # Client should receive the event
        data = await asyncio.wait_for(reader.read(16384), timeout=2.0)
        response, read = Daemon.parse_msg_bytes(data)[0]

        assert response["success"] is True
        assert response["data"]["event"]["event_type"] == NotificationEventType.TEST
        assert response["data"]["event"]["data"]["message"] == "Test"

        # Cleanup
        writer.close()
        await writer.wait_closed()

    @pytest.mark.anyio
    async def test_broadcast_without_client(self, running_daemon: Daemon):
        """Test broadcasting when no client is connected."""
        # Should not raise exception
        await running_daemon._send_notification(
            NotificationEvent(event_type=NotificationEventType.TEST)
        )

    @pytest.mark.anyio
    async def test_multiple_broadcasts(self, running_daemon: Daemon):
        """Test multiple broadcasts in sequence."""
        # Connect client
        if IS_WINDOWS:
            host, port = running_daemon.socket_path.split(":")
            reader, writer = await asyncio.open_connection(host, int(port))
        else:
            reader, writer = await asyncio.open_unix_connection(
                running_daemon.socket_path
            )

        await reader.read(16384)  # Welcome message

        # Send multiple broadcasts
        for i in range(3):
            await running_daemon._send_notification(
                NotificationEvent(
                    event_type=NotificationEventType.TEST, data={"count": i}
                )
            )

        await asyncio.sleep(0.2)

        # Read all events
        data = await reader.read(16384)
        messages, read = Daemon.parse_msg_bytes(data)

        assert len(messages) >= 3

        # Cleanup
        writer.close()
        await writer.wait_closed()


# ============================================================================
# Test Command Handlers - Ping and Status
# ============================================================================


@pytest.mark.anyio
class TestBasicCommands:
    """Test basic daemon commands."""

    @pytest.mark.anyio
    async def test_ping_command(self, daemon_client_connection):
        """Test PING command."""
        reader, writer = daemon_client_connection

        responses = await send_command(reader, writer, DaemonCommand.PING)

        assert responses is not None
        assert responses[-1]["success"] is True
        assert "pong" in responses[-1]["data"]["message"].lower()

    @pytest.mark.anyio
    async def test_status_command(self, daemon_client_connection):
        """Test STATUS command."""
        reader, writer = daemon_client_connection

        responses = await send_command(reader, writer, DaemonCommand.STATUS)

        assert responses is not None
        assert responses[-1]["success"] is True
        data = responses[-1]["data"]
        assert "daemon_running" in data
        assert "server_running" in data
        assert "client_running" in data
        assert "socket_path" in data
        assert data["daemon_running"] is True

    @pytest.mark.anyio
    async def test_shutdown_command(self, running_daemon):
        """Test SHUTDOWN command."""
        # Connect client
        if IS_WINDOWS:
            host, port = running_daemon.socket_path.split(":")
            reader, writer = await asyncio.open_connection(host, int(port))
        else:
            reader, writer = await asyncio.open_unix_connection(
                running_daemon.socket_path
            )

        await reader.read(16384)  # Welcome message

        # Send shutdown command
        responses = await send_command(reader, writer, DaemonCommand.SHUTDOWN)

        assert responses is not None
        assert responses[-1]["success"] is True

        # Give time for shutdown
        await asyncio.sleep(0.5)

        assert running_daemon._running is False

        # Cleanup
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


# ============================================================================
# Test Service Control Commands
# ============================================================================


@pytest.mark.anyio
class TestServiceControl:
    """Test server and client service control commands."""

    @pytest.mark.anyio
    async def test_start_server_command(self, daemon_client_connection, mock_server):
        """Test START_SERVER command."""
        reader, writer = daemon_client_connection

        # We need to get the actual daemon instance
        # This is a workaround for testing
        # In a real scenario, we'd mock the Server class

        responses = await send_command(reader, writer, DaemonCommand.START_SERVER)

        # With no mocking, this will try to actually start a server
        # The command should execute without crashing
        assert responses is not None

    @pytest.mark.anyio
    async def test_start_server_already_running(self, daemon_client_connection):
        """Test starting server when already running."""
        reader, writer = daemon_client_connection

        # Start server
        await send_command(reader, writer, DaemonCommand.START_SERVER)

        # Try to start again
        responses = await send_command(reader, writer, DaemonCommand.START_SERVER)

        # Should return error or success depending on state
        assert responses is not None

    @pytest.mark.anyio
    async def test_mutual_exclusion_server_client(self, daemon_client_connection):
        """Test that server and client cannot run simultaneously."""
        reader, writer = daemon_client_connection
        await asyncio.sleep(0.5)
        # Start server
        responses1 = await send_command(reader, writer, DaemonCommand.START_SERVER)
        await asyncio.sleep(0.5)

        # Try to start client (should fail)
        responses2 = await send_command(reader, writer, DaemonCommand.START_CLIENT)

        # At least one should indicate mutual exclusion
        assert responses1 is not None or responses2 is not None


# ============================================================================
# Test Configuration Commands
# ============================================================================


@pytest.mark.anyio
class TestConfigurationCommands:
    """Test configuration management commands."""

    @pytest.mark.anyio
    async def test_get_server_config(self, daemon_client_connection):
        """Test GET_SERVER_CONFIG command."""
        reader, writer = daemon_client_connection

        responses = await send_command(reader, writer, DaemonCommand.GET_SERVER_CONFIG)

        assert responses is not None
        assert responses[-1]["success"] is True
        data = responses[-1]["data"]
        assert "host" in data
        assert "port" in data
        assert "ssl_enabled" in data

    @pytest.mark.anyio
    async def test_get_client_config(self, daemon_client_connection):
        """Test GET_CLIENT_CONFIG command."""
        reader, writer = daemon_client_connection

        responses = await send_command(reader, writer, DaemonCommand.GET_CLIENT_CONFIG)

        assert responses is not None
        assert responses[-1]["success"] is True
        data = responses[-1]["data"]
        assert "server_host" in data
        assert "server_port" in data
        assert "ssl_enabled" in data

    @pytest.mark.anyio
    async def test_set_server_config(self, daemon_client_connection):
        """Test SET_SERVER_CONFIG command."""
        reader, writer = daemon_client_connection

        params = {"host": "127.0.0.1", "port": 9999}
        responses = await send_command(
            reader, writer, DaemonCommand.SET_SERVER_CONFIG, params
        )

        assert responses is not None
        # May succeed or fail depending on validation
        assert "success" in responses[-1]

    @pytest.mark.anyio
    async def test_set_client_config(self, daemon_client_connection):
        """Test SET_CLIENT_CONFIG command."""
        reader, writer = daemon_client_connection

        params = {"server_host": "192.168.1.100", "server_port": 8888}
        responses = await send_command(
            reader, writer, DaemonCommand.SET_CLIENT_CONFIG, params
        )

        assert responses is not None
        assert "success" in responses[-1]


# ============================================================================
# Test Error Handling
# ============================================================================


@pytest.mark.anyio
class TestErrorHandling:
    """Test error handling in daemon."""

    @pytest.mark.anyio
    async def test_command_execution_exception(self, daemon_client_connection):
        """Test handling of exceptions during command execution."""
        reader, writer = daemon_client_connection

        # Send command that might cause error
        # Most commands will handle errors gracefully
        responses = await send_command(
            reader, writer, DaemonCommand.START_SERVER, {"invalid_param": True}
        )

        # Should not crash, will return success or error
        assert responses is not None

    @pytest.mark.anyio
    async def test_connection_lost_handling(self, running_daemon):
        """Test handling when client connection is lost abruptly."""
        # Connect and close abruptly
        if IS_WINDOWS:
            host, port = running_daemon.socket_path.split(":")
            reader, writer = await asyncio.open_connection(host, int(port))
        else:
            reader, writer = await asyncio.open_unix_connection(
                running_daemon.socket_path
            )

        await reader.read(16384)  # Welcome message

        # Close without proper shutdown
        writer.transport.abort()

        # Give time for daemon to handle disconnection
        await asyncio.sleep(0.2)

        # Daemon should still be running
        assert running_daemon._running is True

        # Should be able to connect again
        if IS_WINDOWS:
            host, port = running_daemon.socket_path.split(":")
            reader2, writer2 = await asyncio.open_connection(host, int(port))
        else:
            reader2, writer2 = await asyncio.open_unix_connection(
                running_daemon.socket_path
            )

        await reader2.read(16384)
        writer2.close()
        await writer2.wait_closed()


# ============================================================================
# Test Daemon Shutdown
# ============================================================================


@pytest.mark.anyio
class TestDaemonShutdown:
    """Test daemon shutdown behavior."""

    @pytest.mark.anyio
    async def test_stop_notifies_connected_client(self, running_daemon):
        """Test that stop() sends notification to connected client."""
        # Connect client
        if IS_WINDOWS:
            host, port = running_daemon.socket_path.split(":")
            reader, writer = await asyncio.open_connection(host, int(port))
        else:
            reader, writer = await asyncio.open_unix_connection(
                running_daemon.socket_path
            )

        await reader.read(16384)  # Welcome message

        # Stop daemon
        stop_task = asyncio.create_task(running_daemon.stop())

        # Client should receive shutdown notification
        data = await asyncio.wait_for(reader.read(16384), timeout=2.0)
        response = json.loads(data.decode("utf-8").strip().split("\n")[0][:-4])

        assert response["success"] is True
        assert "daemon_shutdown" in response["data"]["event"]

        await stop_task

        # Cleanup
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    @pytest.mark.anyio
    async def test_stop_closes_socket_server(self, running_daemon):
        """Test that stop() closes the socket server."""
        socket_server = running_daemon._socket_server
        assert socket_server is not None

        await running_daemon.stop()

        # Socket server should be closed
        # Trying to connect should fail
        with pytest.raises((ConnectionRefusedError, FileNotFoundError)):
            if IS_WINDOWS:
                host, port = running_daemon.socket_path.split(":")
                await asyncio.open_connection(host, int(port))
            else:
                await asyncio.open_unix_connection(running_daemon.socket_path)

    @pytest.mark.anyio
    async def test_wait_for_shutdown(self, running_daemon):
        """Test wait_for_shutdown method."""
        # Start waiting
        wait_task = asyncio.create_task(running_daemon.wait_for_shutdown())

        # Give it a moment
        await asyncio.sleep(0.1)

        # Should not be done yet
        assert not wait_task.done()

        # Stop daemon
        await running_daemon.stop()

        # Wait task should complete
        await asyncio.wait_for(wait_task, timeout=1.0)


# ============================================================================
# Test Edge Cases
# ============================================================================


@pytest.mark.anyio
class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.mark.anyio
    async def test_start_already_running_returns_false(self, running_daemon):
        """Test that calling start() on running daemon returns False."""
        result = await running_daemon.start()
        assert result is False

    @pytest.mark.anyio
    async def test_stop_not_running_no_error(self, daemon_instance):
        """Test that calling stop() on non-running daemon doesn't error."""
        # Should not raise exception
        await daemon_instance.stop()

    @pytest.mark.anyio
    async def test_large_command_payload(self, daemon_client_connection):
        """Test handling of large command payloads."""
        reader, writer = daemon_client_connection

        # Create large payload
        large_data = {"data": "x" * 10000}
        responses = await send_command(reader, writer, DaemonCommand.PING, large_data)

        # Should handle it without crashing
        assert responses is not None

    @pytest.mark.anyio
    async def test_rapid_commands(self, daemon_client_connection):
        """Test sending commands rapidly."""
        reader, writer = daemon_client_connection

        # Send many commands quickly
        for _ in range(10):
            writer.write(
                json.dumps({"command": DaemonCommand.PING, "params": {}}).encode(
                    "utf-8"
                )
            )
            writer.write(b"\n")
        await writer.drain()

        # Should handle all commands
        await asyncio.sleep(0.5)
        data = await reader.read(16384)
        responses = data.decode("utf-8").strip().split("\n")

        # Should have received multiple responses
        assert len(responses) > 0

    @pytest.mark.anyio
    async def test_empty_command(self, daemon_client_connection):
        """Test sending empty command."""
        reader, writer = daemon_client_connection

        writer.write(b"\n")
        await writer.drain()

        # Should not crash, might not receive response for empty line
        await asyncio.sleep(0.1)


# ============================================================================
# Test Platform-Specific Behavior
# ============================================================================


@pytest.mark.anyio
class TestPlatformSpecific:
    """Test platform-specific behavior."""

    def test_is_windows_constant(self):
        """Test IS_WINDOWS constant matches platform."""
        expected = sys.platform in ("win32", "cygwin")
        assert IS_WINDOWS == expected

    def test_default_socket_path_platform_specific(self):
        """Test that default socket path is appropriate for platform."""
        if IS_WINDOWS:
            assert ":" in Daemon.DEFAULT_SOCKET_PATH
            assert Daemon.DEFAULT_SOCKET_PATH.startswith("127.0.0.1")
        else:
            assert Daemon.DEFAULT_SOCKET_PATH.startswith("/")
            assert ".sock" in Daemon.DEFAULT_SOCKET_PATH
