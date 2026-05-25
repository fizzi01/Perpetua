"""Handler for client/server commands arriving on the command stream."""


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

from event import (
    BusEventType,
    CommandEvent,
    EventMapper,
    ActiveScreenChangedEvent,
    ClientMonitorsUpdateCommandEvent,
    ClientMonitorsUpdatedEvent,
    ClientTopologyCommandEvent,
    ClientTopologyUpdatedEvent,
    CrossScreenCommandEvent,
    ClientActiveEvent,
    ForceScreenChangeCommandEvent,
)
from event.bus import EventBus
from network.stream.handler import StreamHandler
from network.protocol.message import MessageType
from utils.logging import get_logger


class CommandHandler:
    """Translates command-stream messages into bus events."""

    def __init__(self, event_bus: EventBus, stream: StreamHandler):
        self.event_bus = event_bus
        self.stream = stream

        self._logger = get_logger(self.__class__.__name__)

        self.stream.register_receive_callback(
            self.handle_command, message_type=MessageType.COMMAND
        )

    async def handle_command(self, message):
        try:
            event = EventMapper.get_event(message)
            if not isinstance(event, CommandEvent):
                self._logger.warning(f"Received non-command event -> {event}")
                return

            if event.command == CommandEvent.CROSS_SCREEN:
                await self.handle_cross_screen(event)
            elif event.command == CommandEvent.FORCE_SCREEN_CHANGE:
                await self.handle_force_screen_change(event)
            elif event.command == CommandEvent.CLIENT_TOPOLOGY:
                await self.handle_client_topology(event)
            elif event.command == CommandEvent.CLIENT_MONITORS_UPDATE:
                await self.handle_client_monitors_update(event)
            else:
                self._logger.warning(f"Unknown command received -> {event.command}")
                return

        except Exception as e:
            self._logger.error(f"{e}")
            return

    async def handle_cross_screen(self, event: CommandEvent):
        # target=="server": client returning the cursor -> notify the
        # cursor guard so the server stops sending to that client.
        # Otherwise the target client must become active.
        crs_event = CrossScreenCommandEvent().from_command_event(event)
        if crs_event.target == "server":
            await self.event_bus.dispatch(
                event_type=BusEventType.SCREEN_CHANGE_GUARD,
                data=ActiveScreenChangedEvent(
                    active_screen=None,
                    source=event.source,
                    position=crs_event.get_position(),
                ),
            )
        else:
            # Carry landing coords + target monitor inside the same
            # event that flips ``_is_active`` so a parallel
            # POSITION_ACTION on the mouse stream can't race the
            # activation and get dropped by the ``_is_active`` gate.
            pos_x, pos_y = crs_event.get_position()
            await self.event_bus.dispatch(
                event_type=BusEventType.CLIENT_ACTIVE,
                data=ClientActiveEvent(
                    client_uid=event.target,
                    client_monitor_id=crs_event.get_client_monitor_id(),
                    position_x=pos_x,
                    position_y=pos_y,
                ),
            )

    async def handle_force_screen_change(self, event: CommandEvent):
        f_ev = ForceScreenChangeCommandEvent().from_command_event(event)
        if event.source == "server" and f_ev.params.get("force", False):
            await self.event_bus.dispatch(
                event_type=BusEventType.CLIENT_INACTIVE, data=None
            )

    async def handle_client_topology(self, event: CommandEvent):
        """Translate a server-pushed topology into a local bus event."""
        topo = ClientTopologyCommandEvent.from_command_event(event)
        await self.event_bus.dispatch(
            event_type=BusEventType.CLIENT_TOPOLOGY_UPDATED,
            data=ClientTopologyUpdatedEvent(
                edge_bindings=topo.get_edge_bindings(),
                server_bbox=topo.get_server_bbox(),
                intra_client_bindings=topo.get_intra_client_bindings(),
            ),
        )

    async def handle_client_monitors_update(self, event: CommandEvent):
        """Server-side dispatch of a client-reported monitor change.

        The client sends this on the command stream when its OS-level
        monitor enumeration changes (display added/removed/resized).
        The service-layer subscriber updates the stored monitor list,
        reconciles placements against the new ids, and pushes refreshed
        edge bindings.
        """
        upd = ClientMonitorsUpdateCommandEvent.from_command_event(event)
        client_uid = upd.get_client_uid()
        if not client_uid:
            self._logger.warning(
                "Dropped client monitors update: missing client_uid in payload"
            )
            return
        await self.event_bus.dispatch(
            event_type=BusEventType.CLIENT_MONITORS_UPDATED,
            data=ClientMonitorsUpdatedEvent(
                client_uid=client_uid,
                monitors=upd.get_monitors(),
            ),
        )
