from event.EventBus import EventBus
from network.stream.GenericStream import StreamHandler

from ._base import BaseClipboardListener, BaseClipboard, BaseClipboardController

# TODO: Implement Windows Clipboard to additionally handle rich content (images, files, etc.)
# class WinClipboard(BaseClipboard):
#     pass

class ClipboardListener(BaseClipboardListener):
    def __init__(self, event_bus: EventBus, stream_handler: StreamHandler, command_stream: StreamHandler,
                 clipboard=BaseClipboard):
        super().__init__(event_bus, stream_handler, command_stream, clipboard)


class ClipboardController(BaseClipboardController):
    def __init__(self, event_bus: EventBus, stream_handler: StreamHandler, clipboard=BaseClipboard):
        super().__init__(event_bus, stream_handler, clipboard)
