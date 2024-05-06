
import platform as _platform

if _platform.system() == 'Windows':
    from . import WinNet as net
elif _platform.system() == 'Linux':
    from. import OSXNet as net
elif _platform.system() == 'Darwin':
    from. import OSXNet as net
else:
    raise OSError("Unsupported platform '{}'".format(_platform.system()))

from .screen import get_screen_size as screen_size

__all__ = ['net', 'screen_size']