from Quartz import CGDisplayBounds, CGMainDisplayID, CGSessionCopyCurrentDictionary

from . import _base


class Screen(_base.Screen):
    @classmethod
    def get_size(cls) -> tuple[int, int]:
        """
        Returns the size of the primary screen as a tuple (width, height).
        """
        mainMonitor = CGDisplayBounds(CGMainDisplayID())
        return mainMonitor.size.width, mainMonitor.size.height

    @classmethod
    def is_screen_locked(cls) -> bool:
        """
        Checks if the screen is currently locked.
        """
        d = CGSessionCopyCurrentDictionary()
        return (
            d.get("CGSSessionScreenIsLocked") and d.get("CGSSessionScreenIsLocked") == 1
        )
