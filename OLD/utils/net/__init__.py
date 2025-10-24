import platform as _platform

if _platform.system() == 'Windows':
    from . import WinNet as NetUtils
elif _platform.system() == 'Linux':
    from . import OSXNet as NetUtils
elif _platform.system() == 'Darwin':
    from . import OSXNet as NetUtils
else:
    raise OSError("Unsupported platform '{}'".format(_platform.system()))

__all__ = ['NetUtils']
