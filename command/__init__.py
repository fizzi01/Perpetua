"""
Contains the logic to handle client/server commands coming from command streams.
"""
from threading import Thread
from event import EventType, CommandEvent, EventMapper
from event.EventBus import EventBus
from network.stream.GenericStream import StreamHandler
from network.protocol.message import MessageType
from utils.logging import Logger


class CommandHandler:
    """
    It registers callback to stream to receive and handle commands.
    It dispatches appropriate events or actions based on the received commands.
    """

    def __init__(self, event_bus: EventBus, stream: StreamHandler):
        self.event_bus = event_bus
        self.stream = stream # StreamHandler for command stream

        self.logger = Logger.get_instance()

        self.stream.register_receive_callback(self.handle_command, message_type=MessageType.COMMAND)


    def handle_command(self, message):
        try:
            event = EventMapper.get_event(message)
            if not isinstance(event, CommandEvent):
                self.logger.log(f"CommandHandler: Received non-command event - {event}", Logger.WARNING)
                return

            # Handle different commands types
            if event.command == CommandEvent.CROSS_SCREEN:
                Thread(target=self.handle_cross_screen, args=(event,)).start()
            else:
                self.logger.log(f"CommandHandler: Unknown command received - {event.command}", Logger.WARNING)
                return

        except Exception as e:
            self.logger.log(f"CommandHandler: Error - {e}", Logger.ERROR)
            return

    def handle_cross_screen(self, event: CommandEvent):
        """
        Handles the cross screen command by dispatching a screen event.
        """
        # If we are server we dispatch ACTIVE_SCREEN_CHANGED event
        # When client sends to server that it crossed the screen, it sends as data the normalized cursor position
        # Then the server should stop data sending to that client by just changing the active screen to None

        if event.target == "server":
            data_dict = event.params
            # Add active_screen info to event params
            data_dict["active_screen"] = None
            data_dict["client"] = event.source

            self.event_bus.dispatch(    # when ServerMouseController receives this event will set the correct cursor position
                event_type=EventType.ACTIVE_SCREEN_CHANGED,
                data=event.params
            )
        else:
            # Dispatch CLIENT_ACTIVE event to notify that client itself is now active
            self.event_bus.dispatch(
                event_type=EventType.CLIENT_ACTIVE, data={})

