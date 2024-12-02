import platform as _platform
from .HandlerInterface import *
from .FileTransferEventHandler import *

if _platform.system() == 'Windows':
    from .WinInputHandler import *
elif _platform.system() == 'Darwin':
    from .OSXInputHandler import *
else:
    raise OSError("Unsupported platform '{}'".format(_platform.system()))
