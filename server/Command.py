from utils.netData import extract_text, extract_command_parts


# Command Pattern per gestire i comandi dei client
class Command:
    def execute(self):
        raise NotImplementedError("Subclasses should implement this!")


class ClipboardCommand(Command):
    def __init__(self, server, data):
        self.server = server
        self.data = data

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
                self.server.reset_mouse("left", self.server.current_mouse_position[1])
        elif self.server.active_screen == "right" and self.direction == "left":
            with self.server.lock:
                self.server.active_screen = None
                self.server._is_transition = False
                self.server.changed.set()
                self.server.reset_mouse("right", self.server.current_mouse_position[1])
        elif self.server.active_screen == "up" and self.direction == "down":
            with self.server.lock:
                self.server.active_screen = None
                self.server._is_transition = False
                self.server.changed.set()
                self.server.reset_mouse("up", self.server.current_mouse_position[0])
        elif self.server.active_screen == "down" and self.direction == "up":
            with self.server.lock:
                self.server.active_screen = None
                self.server._is_transition = False
                self.server.changed.set()
                self.server.reset_mouse("down", self.server.current_mouse_position[0])


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


class CommandFactory:
    @staticmethod
    def create_command(command: [tuple | str], server):
        # Check if command is a tuple
        if not isinstance(command, tuple):  # Commands received from clients
            parts = extract_command_parts(command)
        else:   # Case in which command is already a tuple (internal use)
            parts = command

        if parts[0] == 'clipboard':
            return ClipboardCommand(server, parts[1])
        elif parts[0] == 'return':
            return ReturnCommand(server, parts[1])
        elif parts[0] == 'disconnect':
            return DisconnectCommand(server, parts[1])
        return None
