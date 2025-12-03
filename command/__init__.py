"""
Contains the logic to handle client/server commands coming from command streams.
"""
import asyncio
from event import EventType, CommandEvent, EventMapper
from event.EventBus import EventBus
from network.stream.GenericStream import StreamHandler
from network.protocol.message import MessageType
from utils.logging import Logger


class CommandHandler:
    """
    Async command handler that registers callbacks to stream to receive and handle commands.
    It dispatches appropriate events or actions based on the received commands.
    Now fully async-compatible with AsyncEventBus.
    """

    def __init__(self, event_bus: EventBus, stream: StreamHandler):
        self.event_bus = event_bus
        self.stream = stream # StreamHandler for command stream

        self.logger = Logger.get_instance()

        # Register async callback for command messages
        self.stream.register_receive_callback(self.handle_command, message_type=MessageType.COMMAND)


    async def handle_command(self, message):
        """
        Async callback to handle incoming command messages.
        """
        try:
            event = EventMapper.get_event(message)
            if not isinstance(event, CommandEvent):
                self.logger.log(f"CommandHandler: Received non-command event - {event}", Logger.WARNING)
                return

            # Handle different commands types asynchronously
            if event.command == CommandEvent.CROSS_SCREEN:
                # Create task to handle in background
                asyncio.create_task(self.handle_cross_screen(event))
            else:
                self.logger.log(f"CommandHandler: Unknown command received - {event.command}", Logger.WARNING)
                return

        except Exception as e:
            self.logger.log(f"CommandHandler: Error - {e}", Logger.ERROR)
            return

    async def handle_cross_screen(self, event: CommandEvent):
        """
        Async handler for cross screen command by dispatching a screen event.
        """
        # If we are server we dispatch ACTIVE_SCREEN_CHANGED event
        # When client sends to server that it crossed the screen, it sends as data the normalized cursor position
        # Then the server should stop data sending to that client by just changing the active screen to None

        if event.target == "server":
            data_dict = event.params
            # Add active_screen info to event params
            data_dict["active_screen"] = None
            data_dict["client"] = event.source

            # Async dispatch
            await self.event_bus.dispatch(    # when ServerMouseController receives this event will set the correct cursor position
                event_type=EventType.ACTIVE_SCREEN_CHANGED,
                data=event.params
            )
        else:
            # Dispatch CLIENT_ACTIVE event to notify that client itself is now active
            await self.event_bus.dispatch(
                event_type=EventType.CLIENT_ACTIVE, data={})

