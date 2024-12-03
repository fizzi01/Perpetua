
class ScreenResetStrategyFactory:
    """
    Factory class for creating screen reset strategy objects based on the screen parameter.
    """

    @staticmethod
    def get_reset_strategy(param, server):
        if param == "left":
            return LeftScreenResetStrategy(server)
        elif param == "right":
            return RightScreenResetStrategy(server)
        elif param == "up":
            return UpScreenResetStrategy(server)
        elif param == "down":
            return DownScreenResetStrategy(server)
        else:
            raise ValueError("Invalid screen parameter.")


class ScreenResetStrategy:
    """
    Base class for screen reset strategies.
    """

    def __init__(self, server):
        self.server = server
        self.secure_threshold = 10

    def reset(self, y: float):
        raise NotImplementedError("Subclasses should implement this method.")


class LeftScreenResetStrategy(ScreenResetStrategy):
    def reset(self, y: float):
        self.server.force_mouse_position(self.server.screen_threshold + self.secure_threshold, y)


class RightScreenResetStrategy(ScreenResetStrategy):
    def reset(self, y: float):
        self.server.force_mouse_position(self.server.screen_width - self.server.screen_threshold - self.secure_threshold, y)


class UpScreenResetStrategy(ScreenResetStrategy):
    def reset(self, y: float):
        self.server.force_mouse_position(y, self.server.screen_threshold + self.secure_threshold)


class DownScreenResetStrategy(ScreenResetStrategy):
    def reset(self, y: float):
        # Then move to the desired position
        self.server.force_mouse_position(y, self.server.screen_height - self.server.screen_threshold - self.secure_threshold)
