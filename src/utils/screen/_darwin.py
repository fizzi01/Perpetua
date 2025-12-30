from Quartz import CGDisplayBounds
from Quartz import CGMainDisplayID

from . import _base


class Screen(_base.Screen):
    @classmethod
    def get_size(cls) -> tuple[int, int]:
        """
        Returns the size of the primary screen as a tuple (width, height).
        """
        mainMonitor = CGDisplayBounds(CGMainDisplayID())
        return mainMonitor.size.width, mainMonitor.size.height
