import platform as _platform

if _platform.system() == 'Windows':
    from .WinWindow import DebugWindow as Window
elif _platform.system() == 'Darwin':
    from .OSXWindow import HiddenWindow as Window
else:
    raise OSError("Unsupported platform '{}'".format(_platform.system()))
