"""
Provides mouse input support for macOS (Darwin) systems.
"""

import Quartz
from AppKit import (NSEventTypeGesture,
                    NSEventTypeBeginGesture,
                    NSEventTypeSwipe, NSEventTypeRotate,
                    NSEventTypeMagnify)


from . import _base

def _no_suppress_filter(event_type, event):
    return event

class ServerMouseListener(_base.ServerMouseListener):
    """
    It listens for mouse events on macOS systems.
    Its main purpose is to capture mouse movements and clicks. And handle some border cases like cursor reaching screen edges.
    """
    def _darwin_mouse_suppress_filter(self, event_type, event):
        if self._listening:
            gesture_events = []
            # Tenta di aggiungere le costanti degli eventi gestuali
            try:
                gesture_events.extend([
                    NSEventTypeGesture,
                    NSEventTypeMagnify,
                    NSEventTypeSwipe,
                    NSEventTypeRotate,
                    NSEventTypeBeginGesture,
                ])
            except AttributeError:
                pass

            # Aggiungi i valori numerici per le costanti mancanti
            gesture_events.extend([
                29,  # kCGEventGesture
            ])

            if event_type in [
                Quartz.kCGEventLeftMouseDown,
                Quartz.kCGEventRightMouseDown,
                Quartz.kCGEventOtherMouseDown,
                Quartz.kCGEventLeftMouseDragged,
                Quartz.kCGEventRightMouseDragged,
                Quartz.kCGEventOtherMouseDragged,
                Quartz.kCGEventScrollWheel,

            ] + gesture_events:
                pass
            else:
                return event
        else:
            return event

class ServerMouseController(_base.ServerMouseController):
    """
    It controls the mouse on macOS systems.
    Its main purpose is to move the cursor and simulate mouse clicks.
    """
    pass

class ClientMouseController(_base.ClientMouseController):
    """
    It controls the mouse on macOS systems.
    Its main purpose is to move the cursor and simulate mouse clicks.
    """
    pass
