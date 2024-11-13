
class ScreenTransitionFactory:
    """
    Factory class for creating screen transition state objects based on the active screen.
    """

    @staticmethod
    def get_transition_state(screen, server):
        if screen == "left":
            return LeftScreenTransition(server)
        elif screen == "right":
            return RightScreenTransition(server)
        elif screen == "up":
            return UpScreenTransition(server)
        elif screen == "down":
            return DownScreenTransition(server)
        else:
            return NoScreenTransition(server)


class ScreenTransitionState:
    """
    Base class for screen transition states.
    """

    def __init__(self, server):
        self.server = server

    def handle_transition(self):
        raise NotImplementedError("Subclasses should implement this method.")


class LeftScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        self.server.reset_mouse("right", self.server.current_mouse_position[1])


class RightScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        self.server.reset_mouse("left", self.server.current_mouse_position[1])


class UpScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        self.server.reset_mouse("down", self.server.current_mouse_position[0])


class DownScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        self.server.reset_mouse("up", self.server.current_mouse_position[0])


class NoScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        # No transition needed
        pass
