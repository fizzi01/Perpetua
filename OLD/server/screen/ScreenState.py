# State Pattern per la gestione delle transizioni di schermo
from typing import Optional

from utils.Interfaces import IServerContext


class ScreenState:
    def handle(self):
        raise NotImplementedError("Subclasses should implement this!")


class NoStateTransition(ScreenState):
    def __init__(self, context: IServerContext):
        self.context = context

    def handle(self):
        pass


class NoScreenState(ScreenState):
    def __init__(self, context: IServerContext):
        self.context = context

    def handle(self):
        self.context.set_active_screen(None)
        self.context.mark_transition_changed()


class LeftScreenState(ScreenState):
    def __init__(self, context: IServerContext):
        self.context = context

    def handle(self):
        self.context.set_active_screen("left")
        self.context.mark_transition_changed()
        self.context.reset_mouse("left",
                                 self.context.get_current_mouse_position()[1])


class UpScreenState(ScreenState):
    def __init__(self, context: IServerContext):
        self.context = context

    def handle(self):
        self.context.set_active_screen("up")
        self.context.mark_transition_changed()
        self.context.reset_mouse("up",
                                 self.context.get_current_mouse_position()[0])


class RightScreenState(ScreenState):
    def __init__(self, context: IServerContext):
        self.context = context

    def handle(self):
        self.context.set_active_screen("right")
        self.context.mark_transition_changed()
        self.context.reset_mouse("right",
                                 self.context.get_current_mouse_position()[1])


class DownScreenState(ScreenState):
    def __init__(self, context: IServerContext):
        self.context = context

    def handle(self):
        self.context.set_active_screen("down")
        self.context.mark_transition_changed()
        self.context.reset_mouse("down",
                                 self.context.get_current_mouse_position()[0])


class ScreenStateFactory:
    @staticmethod
    def get_screen_state(screen: Optional[str],
                         context: IServerContext) -> ScreenState:

        # Check if the server is still in transition
        if context.is_transition_blocked():
            return NoStateTransition(context)

        active_screen = context.get_active_screen()

        # First check if the screen is the same as the active screen
        if screen == active_screen:
            return NoStateTransition(context)  # No transition needed

        # Check if the screen is None
        if not screen:
            return NoScreenState(context)

        # Check if the client is present
        if not context.has_client_position(screen):
            return NoStateTransition(context)  # No transition needed

        # Check if client is connected
        if not context.is_client_connected(screen):
            return NoStateTransition(context)  # No transition needed

        # Fall back to None only if both screens are not None
        if screen and active_screen:
            return NoScreenState(context)  # Transition to None

        # Check if the screen is valid
        if screen == "left":
            return LeftScreenState(context)
        elif screen == "right":
            return RightScreenState(context)
        elif screen == "up":
            return UpScreenState(context)
        elif screen == "down":
            return DownScreenState(context)
        else:
            return NoScreenState(context)
