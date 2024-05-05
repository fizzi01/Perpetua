import platform as _platform

if _platform.system() == 'Windows':
    from .WinWindow import TransparentFullscreenWindow as Window
elif _platform.system() == 'Darwin':
    from .OSXWindow import TransparentFullscreenWindow as Window
# elif _platform.system() == 'Linux':
#    from . import LinuxInputHandler as InputHandler
else:
    raise OSError("Unsupported platform '{}'".format(_platform.system()))

__all__ = ['Window']