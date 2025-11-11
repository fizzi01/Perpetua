
from Quartz import CGDisplayBounds
from Quartz import CGMainDisplayID

class Screen:

    @staticmethod
    def get_size() -> tuple[int, int]:
        """
        Returns the size of the primary screen as a tuple (width, height).
        """
        mainMonitor = CGDisplayBounds(CGMainDisplayID())
        return mainMonitor.size.width, mainMonitor.size.height