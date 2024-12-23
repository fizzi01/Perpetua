from typing import Callable

from utils.Interfaces import IScreenContext


class ScreenResetStrategyFactory:
    """
    Factory class for creating screen reset strategy objects based on the screen parameter.
    """

    @staticmethod
    def get_reset_strategy(param: str | None, reset_func: Callable,
                           context: IScreenContext) -> 'ScreenResetStrategy':

        if param == "left":
            return LeftScreenResetStrategy(reset_func, context)
        elif param == "right":
            return RightScreenResetStrategy(reset_func, context)
        elif param == "up":
            return UpScreenResetStrategy(reset_func, context)
        elif param == "down":
            return DownScreenResetStrategy(reset_func, context)
        else:
            raise ValueError("Invalid screen parameter.")


class ScreenResetStrategy:
    """
    Base class for screen reset strategies.
    """

    def __init__(self, reset_func: Callable[[float, float], None], context: IScreenContext):
        self._reset = reset_func
        self.context = context
        self.secure_threshold = 10

    def reset(self, pos: float):
        raise NotImplementedError("Subclasses should implement this method.")


class LeftScreenResetStrategy(ScreenResetStrategy):
    def reset(self, pos: float):
        fixed_axis = self.context.get_screen_treshold() + self.secure_threshold
        self._reset(fixed_axis, pos)


class RightScreenResetStrategy(ScreenResetStrategy):
    def reset(self, pos: float):
        scree_width = self.context.get_screen_size()[0]
        fixed_axis = scree_width - self.context.get_screen_treshold() - self.secure_threshold
        self._reset(fixed_axis, pos)


class UpScreenResetStrategy(ScreenResetStrategy):
    def reset(self, pos: float):
        fixed_axis = self.context.get_screen_treshold() + self.secure_threshold
        self._reset(pos, fixed_axis)


class DownScreenResetStrategy(ScreenResetStrategy):
    def reset(self, y: float):
        fixed_axis = self.context.get_screen_size()[1] - self.context.get_screen_treshold() - self.secure_threshold
        self._reset(y, fixed_axis)
