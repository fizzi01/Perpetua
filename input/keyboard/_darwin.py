import Quartz

from event.bus import EventBus
from network.stream.handler import StreamHandler

from . import _base


class ServerKeyboardListener(_base.ServerKeyboardListener):
    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
        filtering: bool = True,
    ):
        super().__init__(event_bus, stream_handler, command_stream, filtering)

    def _darwin_suppress_filter(self, event_type, event):
        if self._listening:
            flags = Quartz.CGEventGetFlags(event)
            caps_lock = flags & Quartz.kCGEventFlagMaskAlphaShift

            media_volume_event = 14

            if caps_lock != 0:
                return event
            elif event_type == Quartz.kCGEventKeyDown:  # Key press event
                pass
            elif event_type == media_volume_event:
                pass
            else:
                return event
        else:
            return event
