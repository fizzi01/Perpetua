import os

if os.name == "nt":
    from .Server import Server
elif os.name == "posix":
    from .OSXServer import Server
elif os.name == "java":
    from .LinuxServer import Server
else:
    from .Server import Server

__all__ = ['Server']
