import platform as _platform

if _platform.system() == 'Windows':
    from . import WinInputHandler as InputHandler
elif _platform.system() == 'Darwin':
    from . import OSXInputHandler as InputHandler
# elif _platform.system() == 'Linux':
#    from . import LinuxInputHandler as InputHandler
else:
    raise OSError("Unsupported platform '{}'".format(_platform.system()))

__all__ = ['InputHandler']