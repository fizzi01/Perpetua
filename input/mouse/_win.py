"""
Provides mouse input support for Windows systems.
"""
import multiprocessing
from time import time, sleep
from queue import Queue
from threading import Event, Thread
from multiprocessing import Queue as ProcQueue, Event as ProcEvent, Process

from pynput.mouse import Button, Controller as MouseController
from pynput.mouse import Listener as MouseListener

from event import EventType, MouseEvent, CommandEvent, EventMapper
from event.EventBus import EventBus

from network.stream.GenericStream import StreamHandler

from utils.logging import Logger
from utils.screen import Screen

from ._base import EdgeDetector, ScreenEdge


def _no_suppress_filter(msg, data):
    return True


class ServerMouseListener:
    """
    It listens for mouse events on Windows systems.
    Its main purpose is to capture mouse movements and clicks. And handle some border cases like cursor reaching screen edges.
    """

    def __init__(self, event_bus: EventBus, stream_handler: StreamHandler, command_stream: StreamHandler,
                 filtering: bool = True):

        self.stream = stream_handler
        self.command_stream = command_stream
        self.event_bus = event_bus

        self._listening = False
        self._active_screens = {}
        self._screen_size: tuple[int, int] = Screen.get_size()
        self._cross_screen_event = Event()

        self._mouse_filter = self._mouse_suppress_filter if filtering else _no_suppress_filter

        self._listener = MouseListener(on_move=self.on_move, on_scroll=self.on_scroll, on_click=self.on_click,
                                       win32_event_filter=self._mouse_filter)

        # Queue for mouse movements history to detect screen edge reaching
        self._movement_history = Queue(maxsize=5)

        self.logger = Logger.get_instance()

        self.event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED, callback=self._on_active_screen_changed)
        self.event_bus.subscribe(event_type=EventType.CLIENT_CONNECTED, callback=self._on_client_connected)
        self.event_bus.subscribe(event_type=EventType.CLIENT_DISCONNECTED, callback=self._on_client_disconnected)

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

    def _on_client_connected(self, data: dict):
        """
        Event handler for when a client connects.
        """
        client_screen = data.get("client_screen")
        self._active_screens[client_screen] = True

    def _on_client_disconnected(self, data: dict):
        """
        Event handler for when a client disconnects.
        """
        # try to get client from data to remove from active screens
        client = data.get("client_screen")
        if client and client in self._active_screens:
            del self._active_screens[client]

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

            self._cross_screen_event.clear()
        else:
            self._listening = False

    def _mouse_suppress_filter(self, msg, data):

        """
        Suppress mouse events when listening.
        """
        if self._listening:
            # msg = 513/514 -> left down/up
            # msg = 516/517 -> right down/up
            # msg = 519/520 -> middle down/up
            # msg = 522/523 -> scroll
            if msg in (513, 514, 516, 517, 519, 520, 522, 523):
                self._listener._suppress = True
                return False
            else:
                self._listener._suppress = False
        else:
            self._listener._suppress = False

        return True

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

            # Add the current position to the movement history
            try:
                if self._movement_history.full():
                    self._movement_history.get()
                self._movement_history.put((x, y))
            except Exception:
                pass

            if self._movement_history.qsize() >= 2:

                # Check all the previous movements to determine the direction
                queue_data = list(self._movement_history.queue)

                edge = EdgeDetector.is_at_edge(movement_history=queue_data, x=x, y=y, screen_size=self._screen_size)

                mouse_event = MouseEvent(x=x, y=y, action=MouseEvent.POSITION_ACTION)

                try:
                    self._cross_screen_event.set()
                    if edge == ScreenEdge.LEFT and self._active_screens.get("left",
                                                                            False):  # It enters from right edge of the client screen
                        # Normalize position to avoid sticking
                        mouse_event.x = 1
                        mouse_event.y = y / self._screen_size[1]
                        self.event_bus.dispatch(event_type=EventType.ACTIVE_SCREEN_CHANGED,
                                                data={"active_screen": "left"})
                        # Notify client about the active screen change with a CROSS_SCREEN command
                        self.command_stream.send(CommandEvent(command=CommandEvent.CROSS_SCREEN))
                        self.stream.send(mouse_event)
                    elif edge == ScreenEdge.RIGHT and self._active_screens.get("right",
                                                                               False):  # It enters from left edge of the client screen
                        mouse_event.x = 0
                        mouse_event.y = y / self._screen_size[1]
                        self.event_bus.dispatch(event_type=EventType.ACTIVE_SCREEN_CHANGED,
                                                data={"active_screen": "right"})
                        # Notify client about the active screen change with a CROSS_SCREEN command
                        self.command_stream.send(CommandEvent(command=CommandEvent.CROSS_SCREEN))
                        self.stream.send(mouse_event)
                    elif edge == ScreenEdge.TOP and self._active_screens.get("top",
                                                                             False):  # It enters from bottom edge of the client screen
                        mouse_event.x = x / self._screen_size[0]
                        mouse_event.y = 1
                        self.event_bus.dispatch(event_type=EventType.ACTIVE_SCREEN_CHANGED,
                                                data={"active_screen": "top"})
                        # Notify client about the active screen change with a CROSS_SCREEN command
                        self.command_stream.send(CommandEvent(command=CommandEvent.CROSS_SCREEN))
                        self.stream.send(mouse_event)
                    elif edge == ScreenEdge.BOTTOM and self._active_screens.get("bottom",
                                                                                False):  # It enters from top edge of the client screen
                        mouse_event.x = x / self._screen_size[0]
                        mouse_event.y = 0
                        self.event_bus.dispatch(event_type=EventType.ACTIVE_SCREEN_CHANGED,
                                                data={"active_screen": "bottom"})
                        # Notify client about the active screen change with a CROSS_SCREEN command
                        self.command_stream.send(CommandEvent(command=CommandEvent.CROSS_SCREEN))
                        self.stream.send(mouse_event)
                except Exception as e:
                    self.logger.log(f"Failed to dispatch mouse event - {e}", Logger.ERROR)
                finally:
                    self._cross_screen_event.clear()

        return True

    def on_click(self, x, y, button, pressed):
        if self._listening:
            action = MouseEvent.CLICK_ACTION
            mouse_event = MouseEvent(x=x / self._screen_size[0], y=y / self._screen_size[1],
                                     button=button, action=action, is_presed=pressed)
            try:
                self.stream.send(mouse_event)
            except Exception as e:
                self.logger.log(f"Failed to dispatch mouse click event - {e}", Logger.ERROR)
        return True

    def on_scroll(self, x, y, dx, dy):
        if self._listening:
            mouse_event = MouseEvent(x=dx, y=dy, action=MouseEvent.SCROLL_ACTION)
            try:
                self.stream.send(mouse_event)
            except Exception as e:
                self.logger.log(f"Failed to dispatch mouse scroll event - {e}", Logger.ERROR)
        return True


class ServerMouseController:
    """
    It controls the mouse on macOS systems for server side.
    Its main purpose is to move the mouse cursor and perform clicks based on received events.
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus

        self._screen_size: tuple[int, int] = Screen.get_size()

        self._controller = MouseController()
        self.logger = Logger.get_instance()

        # Register for active screen changed events to reposition the cursor
        self.event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED, callback=self._on_active_screen_changed)

    def _on_active_screen_changed(self, data: dict):
        """
        Activate only when the active screen becomes None.
        """
        active_screen = data.get("active_screen")
        if active_screen is None:
            # Get the cursor position from data if available
            x = data.get("x", -1)
            y = data.get("y", -1)
            if x > -1 and y > -1:
                self.position_cursor(x, y)

    def position_cursor(self, x: float | int, y: float | int):
        """
        Position the mouse cursor to the specified (x, y) coordinates.
        """
        try:
            # Denormalize coordinates by mapping into the client screen size
            x *= self._screen_size[0]
            y *= self._screen_size[1]
            x = int(x)
            y = int(y)
        except ValueError:
            self.logger.log(f"Invalid x or y values: x={x}, y={y}", Logger.ERROR)
            return

        self._controller.position = (x, y)


class ClientMouseController:
    """
    It controls the mouse on Windows systems. Its main purpose is to move the mouse cursor and perform clicks based on received events.
    """

    def __init__(self, event_bus: EventBus, stream_handler: StreamHandler, command_stream: StreamHandler):
        self.stream = stream_handler  # Should be a mouse stream
        self.command_stream = command_stream  # Should be a command stream
        self.event_bus = event_bus
        self._cross_screen_event = Event()

        self._is_active = False
        self._screen_size: tuple[int, int] = Screen.get_size()

        # Instead of creating a listener, we just check edge cases after a mouse move event is received
        self._movement_history = Queue(maxsize=5)

        self._controller = MouseController()
        self._pressed = False
        self._last_press_time = -99
        self._doubleclick_counter = 0

        self.logger = Logger.get_instance()

        self._queue: "multiprocessing.Queue" = ProcQueue()
        self._stop_event: "multiprocessing.Event" = ProcEvent()
        self._start_event: "multiprocessing.Event" = ProcEvent()
        self._worker_process: multiprocessing.Process = Process(
            target=ClientMouseController._run_worker,
            args=(self._queue,  self._stop_event,self._start_event, self._screen_size)
        )

        self._worker_started = False

        # Register to receive mouse events from the stream
        self.stream.register_receive_callback(self._mouse_event_callback, message_type="mouse")

        self.event_bus.subscribe(event_type=EventType.CLIENT_ACTIVE, callback=self._on_client_active)
        self.event_bus.subscribe(event_type=EventType.CLIENT_INACTIVE, callback=self._on_client_inactive)

    def start(self):
        """
        Starts the mouse controller worker process.
        """
        if not self._worker_started:
            self._start_event.clear()
            self._worker_process.start()
            sleep(0.5)
            # Check if start_event is clear
            if self._start_event.is_set():
                self._start_event.clear()
                self._worker_started = True
            else:

                self.logger.log(f"Failed to start worker process - {self._start_event}", Logger.ERROR)
                return
            self.logger.log("Client mouse controller worker process started.", Logger.DEBUG)

    def stop(self):
        """
        Stops the mouse controller worker process.
        """
        if self._worker_started:
            self._stop_event.set()
            self._worker_process.join(timeout=1)
            self._worker_started = False
            self.logger.log("Client mouse controller worker process stopped.", Logger.DEBUG)

    @staticmethod
    def _run_worker(queue, stop_event, start_event, screen_size: tuple[int, int]):
        """
        Worker process to handle mouse events.
        """
        start_event.set()
        controller = MouseController()
        pressed = False
        last_press_time = -99
        doubleclick_counter = 0

        while not stop_event.is_set():
            try:
                message = queue.get(timeout=0.1)
                event = EventMapper.get_event(message)
                if not isinstance(event, MouseEvent):
                    continue

                if event.action == MouseEvent.MOVE_ACTION:
                    ClientMouseController.move_cursor(event.x, event.y, event.dx, event.dy, controller, screen_size)
                elif event.action == MouseEvent.POSITION_ACTION:
                    ClientMouseController.position_cursor(event.x, event.y, screen_size, controller)
                elif event.action == MouseEvent.CLICK_ACTION:
                    pressed, last_press_time, doubleclick_counter = ClientMouseController.click(
                        event.button, event.is_pressed, controller, last_press_time, doubleclick_counter, pressed)
                elif event.action == MouseEvent.SCROLL_ACTION:
                    ClientMouseController.scroll(event.dx, event.dy, controller)
            except Exception:
                continue

    def _on_client_active(self, data: dict):
        """
        Event handler for when client becomes active.
        """
        # Reset movement history
        with self._movement_history.mutex:
            self._movement_history.queue.clear()

        self._is_active = True

        self._cross_screen_event.clear()

    def _on_client_inactive(self, data: dict):
        """
        Event handler for when a client becomes inactive.
        """

        self._is_active = False

    def _mouse_event_callback(self, message):
        """
        Callback function to handle mouse events received from the stream.
        The stream will return a ProtocolMessage object, we need to convert it to an Event object through EventMapper.
        """
        try:
            if not self._worker_started:
                try:
                    self.start()
                except Exception as e:
                    pass

            self._queue.put(message)
        except Exception as e:
            self.logger.log(f"ClientMouseController: Failed to process mouse event - {e}", Logger.ERROR)

    def _check_edge(self):
        """
        Check if the mouse cursor is at the edge of the screen and handle accordingly.
        """
        if self._cross_screen_event.is_set():
            return

        # Add the current position to the movement history
        try:
            if self._movement_history.full():
                self._movement_history.get()
            cursor = self._controller.position
            self._movement_history.put((cursor[0], cursor[1]))
        except Exception:
            pass

        if self._is_active:
            if self._movement_history.qsize() >= 2:

                # Get the current cursor position
                x, y = self._controller.position

                # Check all the previous movements to determine the direction
                queue_data = list(self._movement_history.queue)

                edge = EdgeDetector.is_at_edge(movement_history=queue_data, x=x, y=y, screen_size=self._screen_size)

                # If we reach an edge, dispatch event to deactivate client and send cross screen message to server
                try:
                    self._cross_screen_event.set()
                    if edge:
                        # Normalize position to avoid sticking
                        norm_x = x / self._screen_size[0]
                        norm_y = y / self._screen_size[1]
                        screen_data = {"x": norm_x, "y": norm_y}
                        command = CommandEvent(command=CommandEvent.CROSS_SCREEN, params=screen_data)
                        # Send command event to server
                        self.command_stream.send(command)
                        # Dispatch client inactive event
                        self.event_bus.dispatch(event_type=EventType.CLIENT_INACTIVE, data={})
                except Exception as e:
                    self.logger.log(f"Failed to dispatch screen event - {e}", Logger.ERROR)
                finally:
                    self._cross_screen_event.clear()

    @staticmethod
    def position_cursor(x: float | int, y: float | int, screen_size: tuple[int, int], controller: MouseController):
        """
        Position the mouse cursor to the specified (x, y) coordinates.
        """
        try:
            # Denormalize coordinates by mapping into the client screen size
            x *= screen_size[0]
            y *= screen_size[1]
            x = int(x)
            y = int(y)
        except ValueError:
            return

        controller.position = (x, y)

    @staticmethod
    def move_cursor(x: float | int, y: float | int, dx: float | int, dy: float | int, controller: MouseController,
                    screen_size: tuple[int, int]):
        """
        Move the mouse cursor to the specified (x, y) coordinates.
        """

        # if dx and dy are provided, use relative movement
        if x == -1 and y == -1:
            # Convert to int for pynput
            try:
                dx = int(dx)
                dy = int(dy)
            except ValueError:
                dx = 0
                dy = 0

            controller.move(dx=dx, dy=dy)
        else:
            try:
                # Denormalize coordinates by mapping into the client screen size
                x *= screen_size[0]
                y *= screen_size[1]
                x = int(x)
                y = int(y)
            except ValueError:
                return

            controller.position = (x, y)

    @staticmethod
    def click(button: int, is_pressed: bool, controller: MouseController, last_press_time: float,
              doubleclick_counter: int, pressed: bool):
        """
        Perform a mouse click action.
        """
        current_time = time()
        try:
            btn = Button(button)
        except ValueError:
            return

        ret_pressed = pressed
        ret_last_press_time = last_press_time
        ret_doubleclick_counter = doubleclick_counter

        if pressed and not is_pressed:
            controller.release(btn)
            ret_pressed = False
        elif not pressed and is_pressed:
            # If we receive a press event within 200ms of the last press, treat it as a double-click
            if (current_time - last_press_time) < 0.2:
                controller.click(btn, 2 + doubleclick_counter)
                ret_doubleclick_counter = 0 if doubleclick_counter == 2 else 2
                ret_pressed = False
            else:
                controller.press(btn)
                ret_doubleclick_counter = 0
                ret_pressed = True

            ret_last_press_time = current_time

        return ret_pressed, ret_last_press_time, ret_doubleclick_counter

    @staticmethod
    def scroll(dx: int | float, dy: int | float, controller: MouseController):
        """
        Perform a mouse scroll action.
        """
        try:
            dx = int(dx)
            dy = int(dy)
        except ValueError:
            return

        controller.scroll(dx, dy)
