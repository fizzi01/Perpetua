from event.EventBus import EventBus
from network.stream.GenericStream import StreamHandler

from ._base import BaseClipboardListener, BaseClipboard, BaseClipboardController

#TODO: Implement macOS Clipboard to additionally handle rich content (images, files, etc.)
# class DarwinClipboard(BaseClipboard):
#     pass

class ClipboardListener(BaseClipboardListener):
    def __init__(self, event_bus: EventBus, stream_handler: StreamHandler, command_stream: StreamHandler):
        super().__init__(event_bus, stream_handler, command_stream, BaseClipboard) #We impose the clipboard core class here


class ClipboardController(BaseClipboardController):
    def __init__(self, event_bus: EventBus, stream_handler: StreamHandler, clipboard=BaseClipboard):
        super().__init__(event_bus, stream_handler, clipboard)