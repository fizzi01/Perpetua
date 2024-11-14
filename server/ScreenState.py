
# State Pattern per la gestione delle transizioni di schermo
class ScreenState:
    def handle(self):
        raise NotImplementedError("Subclasses should implement this!")


class NoStateTransition(ScreenState):
    def __init__(self, server):
        self.server = server

    def handle(self):
        pass


class NoScreenState(ScreenState):
    def __init__(self, server):
        self.server = server

    def handle(self):
        self.server.active_screen = None
        self.server._is_transition = False
        self.server.changed.set()


class LeftScreenState(ScreenState):
    def __init__(self, server):
        self.server = server

    def handle(self):
        self.server.active_screen = "left"
        self.server._is_transition = False
        self.server.changed.set()
        self.server.reset_mouse("left", self.server.current_mouse_position[1])


class UpScreenState(ScreenState):
    def __init__(self, server):
        self.server = server

    def handle(self):
        self.server.active_screen = "up"
        self.server._is_transition = False
        self.server.changed.set()
        self.server.reset_mouse("up", self.server.current_mouse_position[0])


class RightScreenState(ScreenState):
    def __init__(self, server):
        self.server = server

    def handle(self):
        self.server.active_screen = "right"
        self.server._is_transition = False
        self.server.changed.set()
        self.server.reset_mouse("right", self.server.current_mouse_position[1])


class DownScreenState(ScreenState):
    def __init__(self, server):
        self.server = server

    def handle(self):
        self.server.active_screen = "down"
        self.server._is_transition = False
        self.server.changed.set()
        self.server.reset_mouse("down", self.server.current_mouse_position[0])


class ScreenStateFactory:
    @staticmethod
    def get_screen_state(screen, server):

        # Check if the server is still in transition
        if server.block_transition.is_set():
            return NoStateTransition(server)

        # First check if the screen is the same as the active screen
        if screen == server.active_screen:
            return NoStateTransition(server)  # No transition needed

        # Check if the screen is None
        if not screen:
            return NoScreenState(server)

        # Check if the client is present
        if screen not in server.clients.get_possible_positions():
            return NoStateTransition(server)  # No transition needed

        # Check if client is connected
        if not server.clients.get_connection(screen):
            return NoStateTransition(server)  # No transition needed

        # Fall back to None only if both screens are not None
        if screen and server.active_screen:
            return NoScreenState(server)  # Transition to None

        # Check if the screen is valid
        if screen == "left":
            return LeftScreenState(server)
        elif screen == "right":
            return RightScreenState(server)
        elif screen == "up":
            return UpScreenState(server)
        elif screen == "down":
            return DownScreenState(server)
        else:
            return NoScreenState(server)