from utils.netData import extract_command_parts


class ClientCommand:
    def execute(self):
        raise NotImplementedError("Subclasses should implement this!")


class MouseCommand(ClientCommand):
    def __init__(self, client, data):
        self.client = client
        self.data = data

    def execute(self):
        # Implement the logic to handle mouse commands
        pass


class KeyboardCommand(ClientCommand):
    def __init__(self, client, data):
        self.client = client
        self.data = data

    def execute(self):
        # Implement the logic to handle keyboard commands
        pass


class ClipboardCommand(ClientCommand):
    def __init__(self, client, data):
        self.client = client
        self.data = data

    def execute(self):
        # Implement the logic to handle clipboard commands
        pass


class ClientCommandFactory:
    @staticmethod
    def create_command(command, client):
        parts = extract_command_parts(command)
        if parts[0] == 'mouse':
            return MouseCommand(client, parts[1:])
        elif parts[0] == 'keyboard':
            return KeyboardCommand(client, parts[1:])
        elif parts[0] == 'clipboard':
            return ClipboardCommand(client, parts[1:])
        return None
