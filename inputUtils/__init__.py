import platform as _platform

if _platform.system() == 'Windows':
    from .WinInputHandler import *
elif _platform.system() == 'Darwin':
    from .OSXInputHandler import *
else:
    raise OSError("Unsupported platform '{}'".format(_platform.system()))
