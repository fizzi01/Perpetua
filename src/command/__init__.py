"""
Contains the logic to handle client/server commands coming from command streams.
"""


#  Perpetua - open-source and cross-platform KVM software.
#  Copyright (c) 2026 Federico Izzi.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

import asyncio
from event import (
    EventType,
    CommandEvent,
    EventMapper,
    ActiveScreenChangedEvent,
    CrossScreenCommandEvent,
    ClientActiveEvent,
)
from event.bus import EventBus
from network.stream.handler import StreamHandler
from network.protocol.message import MessageType
from utils.logging import get_logger


class CommandHandler:
    """
    Async command handler that registers callbacks to stream to receive and handle commands.
    It dispatches appropriate events or actions based on the received commands.
    """

    def __init__(self, event_bus: EventBus, stream: StreamHandler):
        self.event_bus = event_bus
        self.stream = stream  # StreamHandler for command stream

        self._logger = get_logger(self.__class__.__name__)

        # Register async callback for command messages
        self.stream.register_receive_callback(
            self.handle_command, message_type=MessageType.COMMAND
        )

    async def handle_command(self, message):
        """
        Async callback to handle incoming command messages.
        """
        try:
            event = EventMapper.get_event(message)
            if not isinstance(event, CommandEvent):
                self._logger.warning(f"Received non-command event -> {event}")
                return

            # Handle different commands types asynchronously
            if event.command == CommandEvent.CROSS_SCREEN:
                # Create task to handle in background
                await asyncio.create_task(self.handle_cross_screen(event))
            else:
                self._logger.warning(f"Unknown command received -> {event.command}")
                return

        except Exception as e:
            self._logger.error(f"{e}")
            return

    async def handle_cross_screen(self, event: CommandEvent):
        """
        Async handler for cross screen command by dispatching a screen event.
        """
        # If we are server we dispatch ACTIVE_SCREEN_CHANGED event
        # When client sends to server that it crossed the screen, it sends as data the normalized cursor position
        # Then the server should stop data sending to that client by just changing the active screen to None
        crs_event = CrossScreenCommandEvent().from_command_event(event)
        if crs_event.target == "server":
            # Async dispatch
            await self.event_bus.dispatch(
                # when ServerMouseController receives this event will set the correct cursor position
                event_type=EventType.SCREEN_CHANGE_GUARD,  # We first notify the cursor guard (cursor handler)
                data=ActiveScreenChangedEvent(
                    active_screen=None,
                    source=event.source,
                    position=crs_event.get_position(),
                ),
            )
        else:
            # Dispatch CLIENT_ACTIVE event to notify that client itself is now active
            await self.event_bus.dispatch(
                event_type=EventType.CLIENT_ACTIVE,
                data=ClientActiveEvent(
                    client_screen=event.target,
                ),
            )
