try:
    from utils.misc.screen import get_screen_size as screen_size
except ImportError:
    # Fallback when screeninfo is not available
    def screen_size():
        return 1920, 1080  # Default screen size

from utils.net.netConstants import *
from utils.Interfaces import *

__all__ = ['screen_size']
