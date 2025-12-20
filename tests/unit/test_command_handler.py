"""
Unit tests for CommandHandler.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from command import CommandHandler
from event import (
    EventType,
    CommandEvent,
    EventMapper,
    ActiveScreenChangedEvent,
    CrossScreenCommandEvent,
    ClientActiveEvent,
)
from network.stream import StreamHandler
from network.protocol.message import MessageType
from tests.unit.conftest import create_protocol_message


# ============================================================================
# Fixtures
# ============================================================================




@pytest.fixture
def mock_stream_handler():
    """Create a mock StreamHandler."""
    stream = MagicMock(spec=StreamHandler)
    stream.register_receive_callback = MagicMock()
    return stream


@pytest.fixture
def command_handler(mock_event_bus, mock_stream_handler):
    """Create a CommandHandler instance for testing."""
    handler = CommandHandler(event_bus=mock_event_bus, stream=mock_stream_handler)
    return handler


@pytest.fixture
def sample_cross_screen_message():
    """Create a sample cross screen command message."""
    return create_protocol_message(
        message_type=MessageType.COMMAND,
        source="client1",
        target="server",
        payload={
            "command": CommandEvent.CROSS_SCREEN,
            "params": {"x": 0.5, "y": 0.3},
        },
    )


@pytest.fixture
def sample_cross_screen_event():
    """Create a sample CrossScreenCommandEvent."""
    return CommandEvent(
        command=CommandEvent.CROSS_SCREEN,
        source="client1",
        target="server",
        params={"x": 0.5, "y": 0.3},
    )


# ============================================================================
# Test CommandHandler Initialization
# ============================================================================


@pytest.mark.asyncio
class TestCommandHandlerInitialization:
    """Test CommandHandler initialization."""

    async def test_handler_initialization(self, mock_event_bus, mock_stream_handler):
        """Test that CommandHandler initializes correctly."""
        handler = CommandHandler(event_bus=mock_event_bus, stream=mock_stream_handler)

        assert handler.event_bus == mock_event_bus
        assert handler.stream == mock_stream_handler
        assert handler._logger is not None

    async def test_handler_registers_callback(
        self, mock_event_bus, mock_stream_handler
    ):
        """Test that handler registers callback with stream."""
        handler = CommandHandler(event_bus=mock_event_bus, stream=mock_stream_handler)

        # Verify callback was registered
        mock_stream_handler.register_receive_callback.assert_called_once()
        call_args = mock_stream_handler.register_receive_callback.call_args

        # Check that handle_command was registered for COMMAND message type
        assert call_args.kwargs["message_type"] == MessageType.COMMAND
        assert call_args.args[0] == handler.handle_command


# ============================================================================
# Test handle_command Method
# ============================================================================


@pytest.mark.asyncio
class TestHandleCommand:
    """Test handle_command method."""

    async def test_handle_command_with_cross_screen(
        self, command_handler, sample_cross_screen_message, mock_event_bus
    ):
        """Test handling cross screen command."""
        with patch.object(
            command_handler, "handle_cross_screen", new_callable=AsyncMock
        ) as mock_handle:
            await command_handler.handle_command(sample_cross_screen_message)

            # Give time for the task to be created and executed
            await asyncio.sleep(0.01)

            # Verify handle_cross_screen was called
            mock_handle.assert_called_once()
            call_args = mock_handle.call_args[0][0]
            assert isinstance(call_args, CommandEvent)
            assert call_args.command == CommandEvent.CROSS_SCREEN

    async def test_handle_command_with_unknown_command(self, command_handler):
        """Test handling unknown command."""
        message = create_protocol_message(
            message_type=MessageType.COMMAND,
            source="client1",
            target="server",
            payload={"command": "unknown_command", "params": {}},
        )

        with patch.object(command_handler._logger, "warning") as mock_warning:
            await command_handler.handle_command(message)
            await asyncio.sleep(0.01)

            # Should log warning
            mock_warning.assert_called()
            assert "Unknown command" in str(mock_warning.call_args)

    async def test_handle_command_with_non_command_event(self, command_handler):
        """Test handling non-command event message."""
        # Create a mouse event message instead of command
        message = create_protocol_message(
            message_type=MessageType.MOUSE,
            source="client1",
            target="server",
            payload={"x": 100, "y": 200, "event": "move"},
        )

        with patch.object(command_handler._logger, "warning") as mock_warning:
            await command_handler.handle_command(message)

            # Should log warning about non-command event
            mock_warning.assert_called_once()
            assert "non-command event" in str(mock_warning.call_args)

    async def test_handle_command_with_exception(self, command_handler):
        """Test error handling in handle_command."""
        message = create_protocol_message(
            message_type=MessageType.COMMAND,
            source="client1",
            target="server",
            payload={"command": CommandEvent.CROSS_SCREEN, "params": {}},
        )

        with patch.object(
            EventMapper, "get_event", side_effect=Exception("Test error")
        ):
            with patch.object(command_handler._logger, "error") as mock_error:
                await command_handler.handle_command(message)

                # Should log error
                mock_error.assert_called_once()
                assert "Error" in str(mock_error.call_args)

    async def test_handle_command_with_malformed_message(self, command_handler):
        """Test handling malformed message."""
        message = create_protocol_message(
            message_type=MessageType.COMMAND,
            source="client1",
            target="server",
            payload={},  # Missing command
        )

        with patch.object(command_handler._logger, "warning") as mock_warning:
            await command_handler.handle_command(message)
            await asyncio.sleep(0.01)

            # Should handle gracefully
            mock_warning.assert_called()


# ============================================================================
# Test handle_cross_screen Method
# ============================================================================


@pytest.mark.asyncio
class TestHandleCrossScreen:
    """Test handle_cross_screen method."""

    async def test_cross_screen_to_server(
        self, command_handler, mock_event_bus, sample_cross_screen_event
    ):
        """Test handling cross screen command targeted to server."""
        await command_handler.handle_cross_screen(sample_cross_screen_event)

        # Verify event was dispatched
        mock_event_bus.dispatch.assert_called_once()
        call_args = mock_event_bus.dispatch.call_args

        # Check event type
        assert call_args.kwargs["event_type"] == EventType.SCREEN_CHANGE_GUARD

        # Check event data
        event_data = call_args.kwargs["data"]
        assert isinstance(event_data, ActiveScreenChangedEvent)
        assert event_data.active_screen is None
        assert event_data.client == "client1"
        assert event_data.x == 0.5
        assert event_data.y == 0.3

    async def test_cross_screen_to_client(self, command_handler, mock_event_bus):
        """Test handling cross screen command targeted to client."""
        event = CommandEvent(
            command=CommandEvent.CROSS_SCREEN,
            source="server",
            target="client1",
            params={"x": 0.8, "y": 0.6},
        )

        await command_handler.handle_cross_screen(event)

        # Verify CLIENT_ACTIVE event was dispatched
        mock_event_bus.dispatch.assert_called_once()
        call_args = mock_event_bus.dispatch.call_args

        # Check event type
        assert call_args.kwargs["event_type"] == EventType.CLIENT_ACTIVE

        # Check event data
        event_data = call_args.kwargs["data"]
        assert isinstance(event_data, ClientActiveEvent)
        assert event_data.client_screen == "client1"

    async def test_cross_screen_with_zero_coordinates(
        self, command_handler, mock_event_bus
    ):
        """Test cross screen with zero coordinates."""
        event = CommandEvent(
            command=CommandEvent.CROSS_SCREEN,
            source="client1",
            target="server",
            params={"x": 0.0, "y": 0.0},
        )

        await command_handler.handle_cross_screen(event)

        # Verify event was dispatched with correct coordinates
        call_args = mock_event_bus.dispatch.call_args
        event_data = call_args.kwargs["data"]
        assert event_data.x == 0.0
        assert event_data.y == 0.0

    async def test_cross_screen_with_extreme_coordinates(
        self, command_handler, mock_event_bus
    ):
        """Test cross screen with extreme coordinates."""
        event = CommandEvent(
            command=CommandEvent.CROSS_SCREEN,
            source="client1",
            target="server",
            params={"x": 1.0, "y": 1.0},
        )

        await command_handler.handle_cross_screen(event)

        # Verify event was dispatched with correct coordinates
        call_args = mock_event_bus.dispatch.call_args
        event_data = call_args.kwargs["data"]
        assert event_data.x == 1.0
        assert event_data.y == 1.0

    async def test_cross_screen_with_missing_coordinates(
        self, command_handler, mock_event_bus
    ):
        """Test cross screen with missing coordinates."""
        event = CommandEvent(
            command=CommandEvent.CROSS_SCREEN,
            source="client1",
            target="server",
            params={},  # No coordinates
        )

        await command_handler.handle_cross_screen(event)

        # Should use default -1 values
        call_args = mock_event_bus.dispatch.call_args
        event_data = call_args.kwargs["data"]
        assert event_data.x == -1
        assert event_data.y == -1


# ============================================================================
# Test CrossScreenCommandEvent Conversion
# ============================================================================


@pytest.mark.asyncio
class TestCrossScreenEventConversion:
    """Test CrossScreenCommandEvent conversion."""

    async def test_from_command_event_conversion(
        self, command_handler, mock_event_bus
    ):
        """Test conversion from CommandEvent to CrossScreenCommandEvent."""
        cmd_event = CommandEvent(
            command=CommandEvent.CROSS_SCREEN,
            source="client2",
            target="server",
            params={"x": 0.75, "y": 0.25},
        )

        await command_handler.handle_cross_screen(cmd_event)

        # Verify conversion happened correctly
        call_args = mock_event_bus.dispatch.call_args
        event_data = call_args.kwargs["data"]

        assert event_data.client == "client2"
        assert event_data.x == 0.75
        assert event_data.y == 0.25

    async def test_cross_screen_event_get_position(self):
        """Test CrossScreenCommandEvent.get_position method."""
        event = CrossScreenCommandEvent(source="client1", target="server", x=0.5, y=0.3)

        position = event.get_position()
        assert position == (0.5, 0.3)

    async def test_cross_screen_event_to_dict(self):
        """Test CrossScreenCommandEvent.to_dict method."""
        event = CrossScreenCommandEvent(source="client1", target="server", x=0.5, y=0.3)

        event_dict = event.to_dict()
        assert event_dict["command"] == CommandEvent.CROSS_SCREEN
        assert event_dict["params"]["x"] == 0.5
        assert event_dict["params"]["y"] == 0.3


# ============================================================================
# Test Integration Scenarios
# ============================================================================


@pytest.mark.asyncio
class TestIntegrationScenarios:
    """Test integration scenarios."""

    async def test_full_cross_screen_flow_to_server(
        self, command_handler, mock_event_bus, sample_cross_screen_message
    ):
        """Test full flow from message to event dispatch for server target."""
        # Simulate receiving message
        await command_handler.handle_command(sample_cross_screen_message)

        # Wait for async task
        await asyncio.sleep(0.05)

        # Verify dispatch was called
        assert mock_event_bus.dispatch.called
        call_args = mock_event_bus.dispatch.call_args

        # Verify correct event type
        assert call_args.kwargs["event_type"] == EventType.SCREEN_CHANGE_GUARD

        # Verify event data
        event_data = call_args.kwargs["data"]
        assert isinstance(event_data, ActiveScreenChangedEvent)
        assert event_data.active_screen is None

    async def test_full_cross_screen_flow_to_client(
        self, command_handler, mock_event_bus
    ):
        """Test full flow from message to event dispatch for client target."""
        message = create_protocol_message(
            message_type=MessageType.COMMAND,
            source="server",
            target="client1",
            payload={
                "command": CommandEvent.CROSS_SCREEN,
                "params": {"x": 0.1, "y": 0.9},
            },
        )

        # Simulate receiving message
        await command_handler.handle_command(message)

        # Wait for async task
        await asyncio.sleep(0.05)

        # Verify dispatch was called
        assert mock_event_bus.dispatch.called
        call_args = mock_event_bus.dispatch.call_args

        # Verify correct event type
        assert call_args.kwargs["event_type"] == EventType.CLIENT_ACTIVE

        # Verify event data
        event_data = call_args.kwargs["data"]
        assert isinstance(event_data, ClientActiveEvent)
        assert event_data.client_screen == "client1"

    async def test_multiple_commands_sequence(self, command_handler, mock_event_bus):
        """Test handling multiple commands in sequence."""
        messages = [
            create_protocol_message(
                message_type=MessageType.COMMAND,
                source=f"client{i}",
                target="server",
                payload={
                    "command": CommandEvent.CROSS_SCREEN,
                    "params": {"x": i * 0.1, "y": i * 0.2},
                },
                sequence_id=i,
            )
            for i in range(3)
        ]

        # Handle all messages
        for message in messages:
            await command_handler.handle_command(message)

        # Wait for all tasks
        await asyncio.sleep(0.1)

        # Verify all dispatches occurred
        assert mock_event_bus.dispatch.call_count == 3

    async def test_concurrent_command_handling(self, command_handler, mock_event_bus):
        """Test handling concurrent commands."""
        messages = [
            create_protocol_message(
                message_type=MessageType.COMMAND,
                source=f"client{i}",
                target="server",
                payload={
                    "command": CommandEvent.CROSS_SCREEN,
                    "params": {"x": 0.5, "y": 0.5},
                },
                sequence_id=i,
            )
            for i in range(5)
        ]

        # Handle all messages concurrently
        await asyncio.gather(
            *[command_handler.handle_command(msg) for msg in messages]
        )

        # Wait for all tasks
        await asyncio.sleep(0.1)

        # All should be handled
        assert mock_event_bus.dispatch.call_count == 5


# ============================================================================
# Test Edge Cases
# ============================================================================


@pytest.mark.asyncio
class TestEdgeCases:
    """Test edge cases."""

    async def test_handle_command_with_none_message(self, command_handler):
        """Test handling None message."""
        with patch.object(command_handler._logger, "error") as mock_error:
            await command_handler.handle_command(None)

            # Should log error
            mock_error.assert_called_once()

    async def test_cross_screen_with_invalid_target(
        self, command_handler, mock_event_bus
    ):
        """Test cross screen with empty/invalid target."""
        event = CommandEvent(
            command=CommandEvent.CROSS_SCREEN,
            source="client1",
            target="",  # Empty target
            params={"x": 0.5, "y": 0.5},
        )

        await command_handler.handle_cross_screen(event)

        # Should still dispatch CLIENT_ACTIVE event (target is falsy but not "server")
        mock_event_bus.dispatch.assert_called_once()

    async def test_cross_screen_with_negative_coordinates(
        self, command_handler, mock_event_bus
    ):
        """Test cross screen with negative coordinates."""
        event = CommandEvent(
            command=CommandEvent.CROSS_SCREEN,
            source="client1",
            target="server",
            params={"x": -0.5, "y": -0.3},
        )

        await command_handler.handle_cross_screen(event)

        # Should accept negative coordinates
        call_args = mock_event_bus.dispatch.call_args
        event_data = call_args.kwargs["data"]
        assert event_data.x == -0.5
        assert event_data.y == -0.3

    async def test_cross_screen_with_large_coordinates(
        self, command_handler, mock_event_bus
    ):
        """Test cross screen with coordinates > 1.0."""
        event = CommandEvent(
            command=CommandEvent.CROSS_SCREEN,
            source="client1",
            target="server",
            params={"x": 5.0, "y": 10.0},
        )

        await command_handler.handle_cross_screen(event)

        # Should accept large coordinates
        call_args = mock_event_bus.dispatch.call_args
        event_data = call_args.kwargs["data"]
        assert event_data.x == 5.0
        assert event_data.y == 10.0

    async def test_handle_command_rapid_succession(self, command_handler):
        """Test handling commands in rapid succession."""
        message = create_protocol_message(
            message_type=MessageType.COMMAND,
            source="client1",
            target="server",
            payload={
                "command": CommandEvent.CROSS_SCREEN,
                "params": {"x": 0.5, "y": 0.5},
            },
        )

        # Handle same message multiple times rapidly
        tasks = [command_handler.handle_command(message) for _ in range(10)]
        await asyncio.gather(*tasks)

        # Wait for all background tasks
        await asyncio.sleep(0.1)

        # Should handle all without errors


# ============================================================================
# Test Error Recovery
# ============================================================================


@pytest.mark.asyncio
class TestErrorRecovery:
    """Test error recovery and resilience."""

    async def test_dispatch_failure_handling(self, command_handler, mock_event_bus):
        """Test handling of dispatch failures."""
        mock_event_bus.dispatch.side_effect = Exception("Dispatch failed")

        event = CommandEvent(
            command=CommandEvent.CROSS_SCREEN,
            source="client1",
            target="server",
            params={"x": 0.5, "y": 0.5},
        )

        # Note: The current implementation does NOT handle exceptions in handle_cross_screen
        # So this test verifies that exceptions propagate
        with pytest.raises(Exception, match="Dispatch failed"):
            await command_handler.handle_cross_screen(event)

    async def test_handler_continues_after_error(self, command_handler, mock_event_bus):
        """Test that handler continues working after an error."""
        # First call fails, second succeeds
        mock_event_bus.dispatch.side_effect = [
            Exception("First call fails"),
            None,  # Second call succeeds
        ]

        event = CommandEvent(
            command=CommandEvent.CROSS_SCREEN,
            source="client1",
            target="server",
            params={"x": 0.5, "y": 0.5},
        )

        # First call should fail
        with pytest.raises(Exception, match="First call fails"):
            await command_handler.handle_cross_screen(event)

        # Second call should work
        await command_handler.handle_cross_screen(event)

        # Verify both calls were attempted
        assert mock_event_bus.dispatch.call_count == 2


# ============================================================================
# Test Message Type Validation
# ============================================================================


@pytest.mark.asyncio
class TestMessageTypeValidation:
    """Test message type validation."""

    async def test_only_command_messages_processed(self, command_handler):
        """Test that only COMMAND message types are processed."""
        # These should be ignored/logged as warnings
        non_command_messages = [
            create_protocol_message(
                message_type=MessageType.MOUSE,
                source="client1",
                target="server",
                payload={"x": 100, "y": 200},
            ),
            create_protocol_message(
                message_type=MessageType.KEYBOARD,
                source="client1",
                target="server",
                payload={"key": "a", "event": "press"},
            ),
            create_protocol_message(
                message_type=MessageType.CLIPBOARD,
                source="client1",
                target="server",
                payload={"content": "test"},
            ),
        ]

        with patch.object(command_handler._logger, "warning") as mock_warning:
            for message in non_command_messages:
                await command_handler.handle_command(message)

            # Should log warnings for non-command events
            assert mock_warning.call_count == len(non_command_messages)

    async def test_command_message_type_accepted(
        self, command_handler, sample_cross_screen_message
    ):
        """Test that COMMAND message type is accepted."""
        with patch.object(
            command_handler, "handle_cross_screen", new_callable=AsyncMock
        ) as mock_handle:
            await command_handler.handle_command(sample_cross_screen_message)
            await asyncio.sleep(0.01)

            # Should be processed
            mock_handle.assert_called_once()

