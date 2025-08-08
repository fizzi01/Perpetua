import logging
from socket import socket
from utils.command.Command import Command
from utils.Interfaces import IBaseSocket, IBaseCommand


class DisconnectCommand(Command):

    DESCRIPTION = "disconnect"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_command = kwargs.get('base_command', None)

    def execute(self):
        logging.debug(f"({self.DESCRIPTION}) Executing command")
        
        # Extract connection from IBaseCommand object or legacy payload
        if self.base_command and hasattr(self.base_command, 'connection'):
            connection = self.base_command.connection
        elif self.payload and len(self.payload) > 0:
            connection = self.payload[0]  # Legacy payload support
        else:
            logging.error(f"({self.DESCRIPTION}) No connection provided")
            return

        if isinstance(connection, socket | IBaseSocket):
            self.context.on_disconnect(connection)
        else:
            logging.debug(f"({self.DESCRIPTION}) Invalid connection type")
