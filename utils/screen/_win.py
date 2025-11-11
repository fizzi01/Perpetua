
from win32api import GetSystemMetrics

class Screen:

    @staticmethod
    def get_size() -> tuple[int, int]:
        """
        Returns the size of the primary screen as a tuple (width, height).
        """
        width = GetSystemMetrics(0)
        height = GetSystemMetrics(1)
        return width, height