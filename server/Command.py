from inputUtils.FileTransferEventHandler import FileTransferEventHandler
from network.IOManager import QueueManager
from utils.Logging import Logger
from utils.netData import extract_text, extract_command_parts


# Command Pattern per gestire i comandi dei client
class Command:
    def execute(self):
        raise NotImplementedError("Subclasses should implement this!")


class ClipboardCommand(Command):
    def __init__(self, server, data):
        self.server = server
        self.data = data
        self.clipboard_sender = QueueManager(None).send_clipboard

    def execute(self):
        text = extract_text(self.data)
        self.server.clipboard_listener.set_clipboard(text)


class ReturnCommand(Command):
    def __init__(self, server, direction):
        self.server = server
        self.direction = direction

    def execute(self):
        # Implementazione della logica di ritorno dello schermo
        if self.server.active_screen == "left" and self.direction == "right":
            with self.server.lock:
                self.server.active_screen = None
                self.server._is_transition = False
                self.server.changed.set()
                self.server.reset_mouse("left", self.server.mouse_listener.get_position()[1])
        elif self.server.active_screen == "right" and self.direction == "left":
            with self.server.lock:
                self.server.active_screen = None
                self.server._is_transition = False
                self.server.changed.set()
                self.server.reset_mouse("right", self.server.mouse_listener.get_position()[1])
        elif self.server.active_screen == "up" and self.direction == "down":
            with self.server.lock:
                self.server.active_screen = None
                self.server._is_transition = False
                self.server.changed.set()
                self.server.reset_mouse("up", self.server.mouse_listener.get_position()[0])
        elif self.server.active_screen == "down" and self.direction == "up":
            with self.server.lock:
                self.server.active_screen = None
                self.server._is_transition = False
                self.server.changed.set()
                self.server.reset_mouse("down", self.server.mouse_listener.get_position()[0])


class DisconnectCommand(Command):
    def __init__(self, server, conn):
        self.server = server
        self.conn = conn

    def execute(self):
        for key in self.server.clients.get_possible_positions():
            if self.server.clients.get_connection(key) == self.conn:
                self.server.clients.remove_connection(key)
                if key == self.server.active_screen:
                    self.server.change_screen()
                return


class FileCopiedCommand(Command):
    def __init__(self, screen, payload):
        self.client = screen
        self.parts = payload
        self.file_event_handler = FileTransferEventHandler()
        self.logger = Logger.get_instance().log

    def execute(self):
        try:
            file_name = self.parts[0]
            file_path = self.parts[2]
            file_size = int(self.parts[1])

            self.file_event_handler.save_file_info(
                owner=self.client,
                file_size=file_size,
                file_name=file_name,
                file_path=file_path
            )

            self.logger(f"File copied registered from {self.client}: {file_path}")
        except Exception as e:
            self.logger(f"Error handling file_copied: {e}", Logger.ERROR)


class FileRequestCommand(Command):
    def __init__(self, requester, payload):
        self.requester = requester
        self.parts = payload
        self.file_event_handler = FileTransferEventHandler()
        self.logger = Logger.get_instance().log

    def execute(self):
        try:
            if len(self.parts) == 1:
                file_path = self.parts[0]
            else:
                file_path = self.parts[1]

            self.file_event_handler.handle_file_request(self.requester)
            self.logger(f"File request received from {self.requester}: {file_path}")
        except Exception as e:
            self.logger(f"Error handling file_request: {e}", Logger.ERROR)


class FileStartCommand(Command):
    def __init__(self, requester, payload):
        self.requester = requester
        self.parts = payload
        self.file_event_handler = FileTransferEventHandler()
        self.logger = Logger.get_instance().log

    def execute(self):
        try:
            file_info = {
                'file_name': self.parts[0],
                'file_size': int(self.parts[1])
            }
            self.file_event_handler.handle_file_start(file_info)
            self.logger(f"File start received from {self.requester}")
        except Exception as e:
            self.logger(f"Error handling file_start: {e}", Logger.ERROR)


class FileChunkCommand(Command):
    def __init__(self, requester, payload):
        self.requester = requester
        self.data = payload
        self.file_event_handler = FileTransferEventHandler()
        self.logger = Logger.get_instance().log

    def execute(self):
        try:
            self.file_event_handler.handle_file_chunk(self.data[1:], self.data[0])
            self.logger(f"File chunk received from {self.requester}")
        except Exception as e:
            self.logger(f"Error handling file_chunk: {e}", Logger.ERROR)


class FileEndCommand(Command):
    def __init__(self, requester):
        self.requester = requester
        self.file_event_handler = FileTransferEventHandler()
        self.logger = Logger.get_instance().log

    def execute(self):
        try:
            self.file_event_handler.handle_file_end()
            self.logger(f"File end received from {self.requester}")
        except Exception as e:
            self.logger(f"Error handling file_end: {e}", Logger.ERROR)


class CommandFactory:
    @staticmethod
    def create_command(command: [tuple | str], server, screen=None):
        # Check if command is a tuple
        if not isinstance(command, tuple):  # Commands received from clients
            parts = extract_command_parts(command)
        else:  # Case in which command is already a tuple (internal use)
            parts = command

        if parts[0] == 'clipboard':
            return ClipboardCommand(server, parts[1])
        elif parts[0] == 'return':
            return ReturnCommand(server, parts[1])
        elif parts[0] == 'disconnect':
            return DisconnectCommand(server, parts[1])
        elif parts[0] == "file_copied":
            return FileCopiedCommand(screen, parts[1:])
        elif parts[0] == "file_request":
            return FileRequestCommand(screen, parts[1:])
        elif parts[0] == "file_start":
            return FileStartCommand(screen, parts[1:])
        elif parts[0] == "file_chunk":
            return FileChunkCommand(screen, parts)
        elif parts[0] == "file_end":
            return FileEndCommand(screen)
        return None
