import platform as _platform

if _platform.system() == 'Windows':
    from .win import common as NetUtils
elif _platform.system() == 'Darwin':
    from .darwin import common as NetUtils
else:
    raise OSError("Unsupported platform '{}'".format(_platform.system()))

__all__ = ['NetUtils']
