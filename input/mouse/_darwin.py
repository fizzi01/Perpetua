"""
Provides mouse input support for macOS (Darwin) systems.
"""
from queue import Queue
from threading import Event

import Quartz
from AppKit import (NSPasteboard,
                    NSFilenamesPboardType,
                    NSEventTypeGesture,
                    NSEventTypeBeginGesture,
                    NSEventTypeSwipe, NSEventTypeRotate,
                    NSEventTypeMagnify)

#from pynput.mouse import Button, Controller as MouseController
from pynput.mouse import Listener as MouseListener

from event.Event import EventType, MouseEvent
from event.EventBus import EventBus
from network.stream.GenericStream import StreamHandler
from utils.logging.logger import Logger

from utils.screen import Screen


def _no_suppress_filter(event_type, event):
    return event


class ServerMouseListener:
    """
    It listens for mouse events on macOS systems.
    Its main purpose is to capture mouse movements and clicks. And handle some border cases like cursor reaching screen edges.
    """
    def __init__(self, event_bus: EventBus, stream_handler: StreamHandler, filtering: bool = False):

        self.stream = stream_handler
        self.event_bus = event_bus

        self._listening = False
        self._screen_size: tuple[int,int] = Screen.get_size()
        self._cross_screen_event = Event()

        self._mouse_filter = self._mouse_suppress_filter if filtering else _no_suppress_filter

        self._listener = MouseListener(on_move=self.on_move, on_scroll=self.on_scroll, on_click=self.on_click,
                                       darwin_intercept=self._mouse_filter)

        # Queue for mouse movements history to detect screen edge reaching
        self._movement_history = Queue(maxsize=5)

        self.logger = Logger.get_instance()

        event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED, callback=self._on_active_screen_changed)

    def start(self):
        """
        Starts the mouse listener.
        """
        self._listener.start()
        self.logger.log("Server mouse listener started.", Logger.DEBUG)

    def stop(self):
        """
        Stops the mouse listener.
        """
        if self.is_alive():
            self._listener.stop()
        self.logger.log("Server mouse listener stopped.", Logger.DEBUG)

    def is_alive(self):
        return self._listener.is_alive()

    def _on_active_screen_changed(self, data: dict):
        """
        Event handler for when the active screen changes.
        """
        # If active screen is not none then we can start listening to mouse events
        active_screen = data.get("active_screen")

        if active_screen is not None:
            self._listening = True
            # reset movement history
            with self._movement_history.mutex:
                self._movement_history.queue.clear()
        else:
            self._listening = False

    def _mouse_suppress_filter(self, event_type, event):

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

    def on_move(self, x, y):
        """
        Event handler for mouse movement.
        While not listening, it needs to check if the cursor is reaching the screen edges.
        """
        if self._cross_screen_event.is_set():
            return True

        # Add the current position to the movement history
        try:
            if self._movement_history.full():
                self._movement_history.get()
            self._movement_history.put((x, y))
        except Exception:
            pass

        # The border check has to take in account only when we are moving forward and not backward or staying still
        if not self._listening:
            if self._movement_history.qsize() >= 2:

                # Check all the previous movements to determine the direction
                queue_data = list(self._movement_history.queue)
                queue_size = len(queue_data)

                moving_towards_left = all(queue_data[i][0] > queue_data[i+1][0] for i in range(queue_size - 1))
                moving_towards_right = all(queue_data[i][0] < queue_data[i+1][0] for i in range(queue_size - 1))
                moving_towards_top = all(queue_data[i][1] > queue_data[i+1][1] for i in range(queue_size - 1))
                moving_towards_bottom = all(queue_data[i][1] < queue_data[i+1][1] for i in range(queue_size - 1))


                # Check if we are at the edges
                at_left_edge = x <= 0
                at_right_edge = x >= self._screen_size[0] - 1
                at_top_edge = y <= 0
                at_bottom_edge = y >= self._screen_size[1] - 1

                mouse_event = MouseEvent(x=x,y=y, action="move")

                try:
                    self._cross_screen_event.set()
                    if at_left_edge and moving_towards_left: # It enters from right edge of the client screen
                            # Normalize position to avoid sticking
                            mouse_event.x = 0
                            mouse_event.y = y / self._screen_size[1]
                            self.event_bus.dispatch(event_type=EventType.ACTIVE_SCREEN_CHANGED,
                                                    data={"active_screen": "left"})
                            self.stream.send(mouse_event)
                    elif at_right_edge and moving_towards_right: # It enters from left edge of the client screen
                            mouse_event.x = 1
                            mouse_event.y = y / self._screen_size[1]
                            self.event_bus.dispatch(event_type=EventType.ACTIVE_SCREEN_CHANGED,
                                                    data={"active_screen": "right"})
                            self.stream.send(mouse_event)
                    elif at_top_edge and moving_towards_top: # It enters from bottom edge of the client screen
                            mouse_event.x = x / self._screen_size[0]
                            mouse_event.y = 0
                            self.event_bus.dispatch(event_type=EventType.ACTIVE_SCREEN_CHANGED,
                                                    data={"active_screen": "top"})
                            self.stream.send(mouse_event)
                    elif at_bottom_edge and moving_towards_bottom: # It enters from top edge of the client screen
                            mouse_event.x = x / self._screen_size[0]
                            mouse_event.y = 1
                            self.event_bus.dispatch(event_type=EventType.ACTIVE_SCREEN_CHANGED,
                                                    data={"active_screen": "bottom"})
                            self.stream.send(mouse_event)
                    else:
                        self._cross_screen_event.clear()
                except Exception as e:
                    self.logger.log(f"Failed to dispatch mouse event - {e}", Logger.ERROR)
                finally:
                    self._cross_screen_event.clear()

        return True

    def on_click(self, x, y, button, pressed):
        if self._listening:
            action = "press" if pressed else "release"
            mouse_event = MouseEvent(x=x, y=y, button=button.value, action=action, is_presed=pressed)
            try:
                self.stream.send(mouse_event)
            except Exception as e:
                self.logger.log(f"Failed to dispatch mouse click event - {e}", Logger.ERROR)
        return True

    def on_scroll(self, x, y, dx, dy):
        if self._listening:
            mouse_event = MouseEvent(x=dx, y=dy, action="scroll")
            try:
                self.stream.send(mouse_event)
            except Exception as e:
                self.logger.log(f"Failed to dispatch mouse scroll event - {e}", Logger.ERROR)
        return True