import logging
from socket import socket
from utils.command.Command import Command
from utils.Interfaces import IBaseSocket


class DisconnectCommand(Command):

    DESCRIPTION = "disconnect"

    def execute(self):
        logging.debug(f"({self.DESCRIPTION}) Executing command")
        connection = self.payload[0]

        if isinstance(connection, socket | IBaseSocket):
            self.context.on_disconnect(connection)
        else:
            logging.debug("({self.DESCRIPTION}) Invalid connection type")
