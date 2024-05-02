import os

if os.name == "nt":
    from .WinWindow import TransparentFullscreenWindow as Window
elif os.name == "posix":
    from .WinWindow import TransparentFullscreenWindow as Window
    # from .OSXServer import Server
elif os.name == "java":
    from .WinWindow import TransparentFullscreenWindow as Window
    # from .LinuxServer import Server
else:
    from .WinWindow import TransparentFullscreenWindow as Window
    # from .Server import Server

__all__ = ['Window']