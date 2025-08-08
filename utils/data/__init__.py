"""
Data object abstraction layer for PyContinuity.
Provides structured data objects that represent the actual objects received through protocol messages.
"""

from .BaseDataObject import IDataObject
from .MouseData import MouseData
from .KeyboardData import KeyboardData
from .ClipboardData import ClipboardData
from .FileData import FileData
from .ReturnData import ReturnData
from .DataObjectFactory import DataObjectFactory

__all__ = [
    'IDataObject',
    'MouseData',
    'KeyboardData', 
    'ClipboardData',
    'FileData',
    'ReturnData',
    'DataObjectFactory'
]