
from win32api import GetSystemMetrics

from . import _base

class Screen(_base.Screen):

    @staticmethod
    def get_size() -> tuple[int, int]:
        """
        Returns the size of the primary screen as a tuple (width, height).
        """
        width = GetSystemMetrics(0)
        height = GetSystemMetrics(1)
        return width, height