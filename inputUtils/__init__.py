import os

if os.name == "nt":
    from . import WinInputHandler as InputHandler
elif os.name == "posix":
    from . import OSXInputHandler as InputHandler
elif os.name == "java":
    from . import LinuxInputHandler as InputHandler
else:
    from . import WinInputHandler as InputHandler

__all__ = ['InputHandler']