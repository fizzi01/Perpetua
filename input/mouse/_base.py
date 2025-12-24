import asyncio
import enum
from collections import deque
from typing import Callable, Optional
from time import time
from threading import Event

from pynput.mouse import Button, Controller as MouseController
from pynput.mouse import Listener as MouseListener

from event import (
    EventType,
    MouseEvent,
    EventMapper,
    CrossScreenCommandEvent,
    ActiveScreenChangedEvent,
    ClientConnectedEvent,
    ClientDisconnectedEvent,
    ClientActiveEvent,
)
from event.bus import EventBus
from model.client import ScreenPosition

from network.stream import StreamType
from network.stream.handler import StreamHandler

from utils.logging import get_logger, Logger
from utils.screen import Screen


class ButtonMapping(enum.Enum):
    """The various buttons.

    The actual values for these items differ between platforms. Some
    platforms may have additional buttons, but these are guaranteed to be
    present everywhere and we remap them to these values.
    """

    #: An unknown button was pressed
    unknown = 0

    #: The left button
    left = 1

    #: The middle button
    middle = 2

    #: The right button
    right = 3


class ScreenEdge(enum.Enum):
    LEFT = 1
    RIGHT = 2
    TOP = 3
    BOTTOM = 4


class EdgeDetector:
    """
    A utility class for detecting when the mouse cursor reaches the edges of the screen.
    """

    @staticmethod
    def is_at_edge(
        movement_history: list, x: float | int, y: float | int, screen_size: tuple
    ) -> Optional[ScreenEdge]:
        """
        Determines if the cursor is moving towards and has reached any edge of the screen.

        Args:
            movement_history (list): A list of recent (x, y) positions of the cursor.
            x (float | int): Current x position of the cursor.
            y (float | int): Current y position of the cursor.
            screen_size (tuple): A tuple representing the screen size (width, height).
        Returns:
            Optional[ScreenEdge]: The edge the cursor is at, or None if not at any
        """
        # Check all the previous movements to determine the direction
        queue_data = movement_history
        queue_size = len(queue_data)

        moving_towards_left = all(
            queue_data[i][0] > queue_data[i + 1][0] for i in range(queue_size - 1)
        )
        moving_towards_right = all(
            queue_data[i][0] < queue_data[i + 1][0] for i in range(queue_size - 1)
        )
        moving_towards_top = all(
            queue_data[i][1] > queue_data[i + 1][1] for i in range(queue_size - 1)
        )
        moving_towards_bottom = all(
            queue_data[i][1] < queue_data[i + 1][1] for i in range(queue_size - 1)
        )

        # Check if we are at the edges
        at_left_edge = x <= 0
        at_right_edge = x >= screen_size[0] - 1
        at_top_edge = y <= 0
        at_bottom_edge = y >= screen_size[1] - 1

        edge = None

        if at_left_edge and moving_towards_left:
            edge = ScreenEdge.LEFT
        elif at_right_edge and moving_towards_right:
            edge = ScreenEdge.RIGHT
        elif at_top_edge and moving_towards_top:
            edge = ScreenEdge.TOP
        elif at_bottom_edge and moving_towards_bottom:
            edge = ScreenEdge.BOTTOM

        return edge

    def detect_edge(
        self,
        movement_history: list,
        x: float | int,
        y: float | int,
        screen_size: tuple,
        callbacks: dict[ScreenEdge, Callable],
    ):
        """
        Detects if the cursor is at the edge and invokes the appropriate callback.

        Args:
            movement_history (list): A list of recent (x, y) positions of the cursor.
            x (float | int): Current x position of the cursor.
            y (float | int): Current y position of the cursor.
            screen_size (tuple): A tuple representing the screen size (width, height).
        """
        edge = self.is_at_edge(movement_history, x, y, screen_size)
        if edge and edge in callbacks:
            callbacks[edge]()

    @staticmethod
    def get_crossing_coords(
        x: float | int,
        y: float | int,
        screen_size: tuple,
        edge: ScreenEdge,
        screen: str | None,
    ) -> tuple[float, float]:
        """
        Get the coordinates when crossing back from client to server.
        Coords will be the opposite of the real one (so opposite to the edge reached).

        Args:
            x (float | int): Current x position of the cursor.
            y (float | int): Current y position of the cursor.
            screen_size (tuple): A tuple representing the screen size (width, height).
            edge (ScreenEdge): The edge that was reached.
        Returns:
            tuple[float, float]: The normalized crossing coordinates.
        """
        if screen == "" or screen is None:
            return -1, -1

        # If we reach the bottom edge, we need to set y to 1 (top of the server screen)
        if edge == ScreenEdge.BOTTOM and screen == ScreenPosition.TOP:
            return x / screen_size[0], 0.0
        # If we reach the top edge, we need to set y to 0 (bottom of the server screen)
        elif edge == ScreenEdge.TOP and screen == ScreenPosition.BOTTOM:
            return x / screen_size[0], 1.0
        # If we reach the left edge, we need to set x to 1 (right of the server screen)
        elif edge == ScreenEdge.LEFT and screen == ScreenPosition.RIGHT:
            return 1.0, y / screen_size[1]
        # If we reach the right edge, we need to set x to 0 (left of the server screen)
        elif edge == ScreenEdge.RIGHT and screen == ScreenPosition.LEFT:
            return 0.0, y / screen_size[1]
        else:
            return -1, -1


# TODO : We need to check if a mouse is available
# TODO: Improve crossing edge detection to avoid false positives during crossing
class ServerMouseListener(object):
    """
    Base class for server-side mouse listeners.
    Its main purpose is to listen to mouse events and dispatch them
    """

    MOVEMENT_HISTORY_N_THRESHOLD = 6
    MOVEMENT_HISTORY_LEN = 8

    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
        filtering: bool = True,
    ):
        """
        Initializes the server mouse listener.

        Args:
            event_bus (EventBus): The event bus for dispatching events.
            stream_handler (StreamHandler): The stream handler for mouse events.
            command_stream (StreamHandler): The stream handler for command events.
            filtering (bool): Whether to apply platform-specific mouse event filtering.
        """

        self.stream = stream_handler  # Should be a mouse stream
        self.command_stream = command_stream
        self.event_bus = event_bus

        self._listening = False
        self._active_screens = {}
        self._screen_size: tuple[int, int] = Screen.get_size()
        self._cross_screen_event = Event()
        self._cross_screen_lock = asyncio.Lock()
        self._handling_cross_screen = False

        # Check platform to set appropriate mouse filter
        self._filter_args = {}
        if filtering:
            try:
                import platform

                current_platform = platform.system()
                if current_platform == "Darwin":
                    self._filter_args["darwin_intercept"] = (
                        self._darwin_mouse_suppress_filter
                    )
                elif current_platform == "Windows":
                    self._filter_args["win32_event_filter"] = (
                        self._win32_mouse_suppress_filter
                    )
            except Exception:
                pass

        self._listener = None

        # Queue for mouse movements history to detect screen edge reaching
        self._movement_history = deque(maxlen=self.MOVEMENT_HISTORY_LEN)

        self._logger = get_logger(self.__class__.__name__)

        # Store event loop reference for thread-safe async scheduling
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running yet - will be set when start() is called
            self._loop = None

        # Subscribe with async callbacks
        self.event_bus.subscribe(
            event_type=EventType.ACTIVE_SCREEN_CHANGED,
            callback=self._on_active_screen_changed,
            priority=True,
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_CONNECTED,
            callback=self._on_client_connected,
            priority=True,
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected,
            priority=True,
        )

    def _create_listener(self) -> MouseListener:
        """
        Creates a new mouse listener instance.
        """
        return MouseListener(
            on_move=self.on_move,
            on_scroll=self.on_scroll,
            on_click=self.on_click,
            **self._filter_args,
        )

    def start(self) -> bool:
        """
        Starts the mouse listener.
        """
        # Capture event loop reference if not already set
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._logger.warning(
                    "No event loop running. "
                    "Mouse listener must be started within an async context."
                )

        if not self.is_alive():
            self._listener = self._create_listener()
            self._listener.start()
        self._logger.debug("Started.")
        return True

    def stop(self) -> bool:
        """
        Stops the mouse listener.
        """
        if self._listener is not None and self.is_alive():
            self._listener.stop()
        self._logger.debug("Stopped.")
        return True

    def is_alive(self):
        return self._listener.is_alive() if self._listener else False

    async def _on_client_connected(self, data: Optional[ClientConnectedEvent]):
        """
        Async event handler for when a client connects.
        """
        if data is None:
            return

        client_screen = data.client_screen
        # We need this check in order to not dispatch cross-screen events to clients without mouse stream
        client_streams = data.streams  # We check if client has mouse stream enabled
        if client_streams is None:
            return

        if client_screen and StreamType.MOUSE in client_streams:  # If not, we ignore
            self._active_screens[client_screen] = True

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        """
        Async event handler for when a client disconnects.
        """
        if data is None:
            return

        # try to get client from data to remove from active screens
        client = data.client_screen
        if client and client in self._active_screens:
            del self._active_screens[client]

        # if active screens is empty, we stop listening
        if len(self._active_screens.items()) == 0:
            self._listening = False

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        """
        Async event handler for when the active screen changes.
        """
        if data is None:
            return

        # If active screen is not none then we can start listening to mouse events
        active_screen = data.active_screen

        if active_screen is not None:
            self._movement_history.clear()
            self._listening = True
            self._cross_screen_event.clear()
        else:
            self._listening = False

    def _darwin_mouse_suppress_filter(self, event_type, event):
        raise NotImplementedError("Mouse suppress filter not implemented yet.")

    def _win32_mouse_suppress_filter(self, msg, data):
        raise NotImplementedError("Mouse suppress filter not implemented yet.")

    def on_move(self, x, y):
        """
        Event handler for mouse movement.
        While not listening, it needs to check if the cursor is reaching the screen edges.
        Since pynput runs in its own thread, we need to schedule async operations.
        """
        if self._cross_screen_event.is_set() or self._handling_cross_screen:
            return True

        # The border check has to take in account only when we are moving forward and not backward or staying still
        if not self._listening:
            # Add the current position to the movement history
            try:
                self._movement_history.append((x, y))
            except Exception:
                pass

            if len(self._movement_history) >= self.MOVEMENT_HISTORY_N_THRESHOLD:
                # Check all the previous movements to determine the direction
                queue_data = list(self._movement_history)

                edge = EdgeDetector.is_at_edge(
                    movement_history=queue_data, x=x, y=y, screen_size=self._screen_size
                )
                if edge is None:
                    return True

                mouse_event = MouseEvent(x=x, y=y, action=MouseEvent.POSITION_ACTION)

                try:
                    self._cross_screen_event.set()
                    if edge == ScreenEdge.LEFT and self._active_screens.get(
                        ScreenPosition.LEFT, False
                    ):
                        # Normalize position to avoid sticking
                        mouse_event.x = 1
                        mouse_event.y = y / self._screen_size[1]
                        # Schedule async operations
                        self._schedule_async(
                            self._handle_cross_screen(
                                edge, mouse_event, ScreenPosition.LEFT
                            )
                        )
                    elif edge == ScreenEdge.RIGHT and self._active_screens.get(
                        ScreenPosition.RIGHT, False
                    ):
                        mouse_event.x = 0
                        mouse_event.y = y / self._screen_size[1]
                        self._schedule_async(
                            self._handle_cross_screen(
                                edge, mouse_event, ScreenPosition.RIGHT
                            )
                        )
                    elif edge == ScreenEdge.TOP and self._active_screens.get(
                        ScreenPosition.TOP, False
                    ):
                        mouse_event.x = x / self._screen_size[0]
                        mouse_event.y = 1
                        self._schedule_async(
                            self._handle_cross_screen(
                                edge, mouse_event, ScreenPosition.TOP
                            )
                        )
                    elif edge == ScreenEdge.BOTTOM and self._active_screens.get(
                        ScreenPosition.BOTTOM, False
                    ):
                        mouse_event.x = x / self._screen_size[0]
                        mouse_event.y = 0
                        self._schedule_async(
                            self._handle_cross_screen(
                                edge, mouse_event, ScreenPosition.BOTTOM
                            )
                        )
                except Exception as e:
                    self._logger.error(f"Failed to dispatch mouse event -> {e}")
                finally:
                    self._cross_screen_event.clear()

        return True

    def _schedule_async(self, coro):
        """
        Helper to schedule async coroutines from sync context (pynput thread).
        Uses saved event loop reference for thread-safe scheduling.
        """
        if self._loop is not None and not self._loop.is_closed():
            # Best case: we have a valid loop reference
            try:
                asyncio.run_coroutine_threadsafe(coro, self._loop)
                return
            except Exception as e:
                self._logger.error(f"Error scheduling coroutine -> {e}")

        # Fallback: try to get running loop
        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            # Last resort: try to get event loop (may not work from thread)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(coro, loop)
                else:
                    self._logger.warning(
                        "Event loop not running. Cannot schedule async operation"
                    )
            except Exception as e:
                self._logger.warning(
                    f"No event loop available for async operation -> {e}"
                )

    async def _handle_cross_screen(
        self, edge: ScreenEdge, mouse_event: MouseEvent, screen: str
    ):
        """Async handler for cross-screen events"""
        # reset movement history
        try:
            # Acquisisci il lock per evitare cross-screen concorrenti
            async with self._cross_screen_lock:
                if (
                    len(self._movement_history) < self.MOVEMENT_HISTORY_N_THRESHOLD
                ):  # Check again because of async
                    return

                # Reset movement history
                self._movement_history.clear()

                # We notify the system that an active screen change has occurred
                await self.event_bus.dispatch(
                    event_type=EventType.SCREEN_CHANGE_GUARD,  # We first notify the cursor guard (cursor handler)
                    data=ActiveScreenChangedEvent(active_screen=screen),
                )

                # Attendi il completamento dell'invio dei messaggi
                await self.command_stream.send(CrossScreenCommandEvent())
                await self.stream.send(mouse_event)
                await asyncio.sleep(0)

        except Exception as e:
            self._logger.error(f"Error handling cross-screen -> {e}")
        finally:
            # Resetta gli stati solo dopo il completamento di tutte le operazioni async
            self._handling_cross_screen = False
            self._cross_screen_event.clear()

    def on_click(self, x, y, button: Button, pressed):
        if self._listening:
            mouse_event = MouseEvent(
                x=x / self._screen_size[0],
                y=y / self._screen_size[1],
                button=ButtonMapping[button.name].value,
                action=MouseEvent.CLICK_ACTION,
                is_presed=pressed,
            )
            try:
                # Schedule async send
                self._schedule_async(self.stream.send(mouse_event))
            except Exception as e:
                self._logger.error(f"Failed to dispatch mouse click event -> {e}")
        return True

    def on_scroll(self, x, y, dx, dy):
        if self._listening:
            mouse_event = MouseEvent(dx=dx, dy=dy, action=MouseEvent.SCROLL_ACTION)
            try:
                # Schedule async send
                self._schedule_async(self.stream.send(mouse_event))
            except Exception as e:
                self._logger.error(f"Failed to dispatch mouse scroll event -> {e}")
        return True


class ServerMouseController(object):
    """
    Base class for server-side mouse controllers.
    Its main purpose is to control the mouse cursor position
    when the active screen changes.
    """

    def __init__(self, event_bus: EventBus):
        """
        Initializes the server mouse controller.

        Args:
            event_bus (EventBus): The event bus for dispatching events.
        """
        self.event_bus = event_bus

        self._screen_size: tuple[int, int] = Screen.get_size()

        self._controller = MouseController()
        self._logger = get_logger(self.__class__.__name__)

        # Register for active screen changed events to reposition the cursor
        self.event_bus.subscribe(
            event_type=EventType.ACTIVE_SCREEN_CHANGED,
            callback=self._on_active_screen_changed,
        )

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        """
        Activate only when the active screen becomes None.
        """
        if data is not None:
            active_screen = data.active_screen
            if active_screen is None:
                # Get the cursor position from data if available
                x = data.x
                y = data.y
                if x > -1 and y > -1:
                    # We need to asyncronously position the cursor multiple times to ensure it works across platforms
                    for _ in range(3):
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
            self._logger.log(f"Invalid x or y values: x={x}, y={y}", Logger.ERROR)
            return

        self._controller.position = (x, y)


# TODO: Optimize edge detection to avoid false positives during crossing
class ClientMouseController(object):
    """
    Async mouse controller for client side.
    Handles mouse movements, clicks, and scrolls based on received events.
    Converted from multiprocessing to fully async with asyncio tasks.
    """

    MOVEMENT_HISTORY_N_THRESHOLD = 6
    MOVEMENT_HISTORY_LEN = 8

    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
    ):
        """
        Initializes the client mouse controller.

        Args:
            event_bus (EventBus): The event bus for dispatching events.
            stream_handler (StreamHandler): The stream handler for mouse events.
            command_stream (StreamHandler): The stream handler for command events.
        """
        self.stream = stream_handler  # Should be a mouse stream
        self.command_stream = command_stream  # Should be a command stream
        self.event_bus = event_bus
        self._cross_screen_event = asyncio.Event()
        self._edge_check_lock = asyncio.Lock()
        self._checking_edge = False

        self._is_active = False
        self._current_screen = None
        self._screen_size: tuple[int, int] = Screen.get_size()

        # Instead of creating a listener, we just check edge cases after a mouse move event is received
        # Using deque for better performance and async compatibility
        self._movement_history = deque(maxlen=self.MOVEMENT_HISTORY_LEN)

        self._controller = MouseController()
        self._pressed = False
        self._last_press_time = -99
        self._doubleclick_counter = 0

        self._logger = get_logger(self.__class__.__name__)

        # Async queue instead of multiprocessing queue
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

        # Register to receive mouse events from the stream (async callback)
        self.stream.register_receive_callback(
            self._mouse_event_callback, message_type="mouse"
        )

        # Subscribe with async callbacks
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_ACTIVE, callback=self._on_client_active
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_INACTIVE, callback=self._on_client_inactive
        )

    async def start(self):
        """
        Starts the async mouse controller worker task.
        """
        if not self._running:
            self._running = True
            # Clear queue
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Start worker task
            self._worker_task = asyncio.create_task(self._run_worker())
            self._logger.log("Async worker started.", Logger.DEBUG)

    async def stop(self):
        """
        Stops the async mouse controller worker task.
        """
        if self._running:
            self._running = False

            if self._worker_task:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
                self._worker_task = None

            self._logger.log("Async worker stopped.", Logger.DEBUG)

    def is_alive(self) -> bool:
        """
        Checks if the async mouse controller worker task is running.
        """
        return (
            self._running
            and self._worker_task is not None
            and not self._worker_task.done()
        )

    async def _run_worker(self):
        """
        Async worker task to handle mouse events.
        Replaces the multiprocessing worker.
        """
        # loop = asyncio.get_running_loop()

        while self._running:
            try:
                # Get message from async queue
                message = await self._queue.get()

                event = EventMapper.get_event(message)
                if not isinstance(event, MouseEvent):
                    continue

                # TODO: Benchamrk to see if not using run_in_executor for move has a significant impact on performance

                # Execute mouse actions in executor to avoid blocking
                if event.action == MouseEvent.MOVE_ACTION:
                    # await loop.run_in_executor(
                    #     None,
                    #     self._move_cursor,
                    #     event.x, event.y, event.dx, event.dy
                    # )
                    self._move_cursor(event.x, event.y, event.dx, event.dy)
                    # Check for edge crossing after movement
                    await self._check_edge()
                elif event.action == MouseEvent.POSITION_ACTION:
                    # await loop.run_in_executor(
                    #     None,
                    #     self._position_cursor,
                    #     event.x, event.y
                    # )
                    for _ in range(
                        3
                    ):  # We position multiple times to ensure it works across platforms
                        asyncio.create_task(self._position_cursor(event.x, event.y))
                    # Check for edge crossing after positioning
                    await self._check_edge()
                elif event.action == MouseEvent.CLICK_ACTION:
                    # Click is fast enough to run directly
                    self._click(event.button, event.is_pressed)
                elif event.action == MouseEvent.SCROLL_ACTION:
                    # await loop.run_in_executor(
                    #     None,
                    #     self._scroll,
                    #     event.dx, event.dy
                    # )
                    self._scroll(event.dx, event.dy)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.log(f"Error in worker -> {e}", Logger.ERROR)
                await asyncio.sleep(0.01)

    async def _on_client_active(self, data: Optional[ClientActiveEvent]):
        """
        Async event handler for when client becomes active.
        """
        if data is not None:
            self._current_screen = data.client_screen
        # Reset movement history
        self._movement_history.clear()

        self._is_active = True
        self._cross_screen_event.clear()

        # Auto-start if not running
        if not self._running:
            await self.start()

    async def _on_client_inactive(self, data: Optional[ClientActiveEvent]):
        """
        Async event handler for when a client becomes inactive.
        """
        # Reset movement history
        self._movement_history.clear()
        self._cross_screen_event.clear()
        self._is_active = False

    async def _mouse_event_callback(self, message):
        """
        Async callback function to handle mouse events received from the stream.
        """
        try:
            # Auto-start if not running
            if not self._running:
                await self.start()

            # Ignore events if crossing screen or inactive
            if self._cross_screen_event.is_set() or not self._is_active:
                return await asyncio.sleep(0)

            # Put message in async queue
            await self._queue.put(message)
            return None
        except Exception as e:
            self._logger.log(f"Failed to process mouse event -> {e}", Logger.ERROR)
            return None

    async def _check_edge(self):
        """
        Check if the mouse cursor is at the edge of the screen and handle accordingly.
        This is called after cursor movement. Optimized for maximum speed.
        """
        if (
            self._checking_edge
            or self._cross_screen_event.is_set()
            or not self._is_active
        ):
            return await asyncio.sleep(0)

        try:
            # Acquisisci il lock per serializzare i controlli edge
            async with self._edge_check_lock:
                # Double-check dopo aver acquisito il lock
                if self._cross_screen_event.is_set() or not self._is_active:
                    return await asyncio.sleep(0)

                self._checking_edge = True

                # Get the current cursor position
                x, y = self._controller.position

                # Add the current position to the movement history
                self._movement_history.append((x, y))

                # Need at least 2 positions to determine direction
                if len(self._movement_history) < self.MOVEMENT_HISTORY_N_THRESHOLD:
                    return None

                # Convert deque to list for edge detection
                queue_data = list(self._movement_history)

                edge = EdgeDetector.is_at_edge(
                    movement_history=queue_data, x=x, y=y, screen_size=self._screen_size
                )

                # If we reach an edge, dispatch event to deactivate client and send cross screen message to server
                if edge:
                    x, y = EdgeDetector.get_crossing_coords(
                        x=x,
                        y=y,
                        screen_size=self._screen_size,
                        edge=edge,
                        screen=self._current_screen,
                    )

                    if x == -1 and y == -1:
                        # Invalid crossing coords for current screen setup
                        return None

                    # Set event BEFORE clearing history to block concurrent checks
                    self._cross_screen_event.set()

                    # Clear movement history atomically
                    self._movement_history.clear()

                    command = CrossScreenCommandEvent(x=x, y=y)

                    # Send command and dispatch event sequentially
                    await self.command_stream.send(command)
                    await self.event_bus.dispatch(
                        event_type=EventType.CLIENT_INACTIVE, data=None
                    )

                    # Piccolo delay per garantire che i messaggi siano stati processati
                    return await asyncio.sleep(0)

        except Exception as e:
            self._logger.log(f"Failed to dispatch screen event -> {e}", Logger.ERROR)
        finally:
            self._checking_edge = False

        return await asyncio.sleep(0)

    async def _position_cursor(self, x: float | int, y: float | int):
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
            return

        self._controller.position = (x, y)

    def _move_cursor(
        self, x: float | int, y: float | int, dx: float | int, dy: float | int
    ):
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

            self._controller.move(dx=dx, dy=dy)
        else:
            try:
                # Denormalize coordinates by mapping into the client screen size
                x *= self._screen_size[0]
                y *= self._screen_size[1]
                x = int(x)
                y = int(y)
            except ValueError:
                return

            self._controller.position = (x, y)

    def _click(self, button: int | None, is_pressed: bool):
        """
        Perform a mouse click action.
        """
        current_time = time()
        try:
            # Get button name from button value
            name = ButtonMapping(button).name
            btn = Button[name]
        except ValueError:
            return

        if self._pressed and not is_pressed:
            self._controller.release(btn)
            self._pressed = False
        elif not self._pressed and is_pressed:
            # If we receive a press event within 200ms of the last press, treat it as a double-click
            if (current_time - self._last_press_time) < 0.2:
                self._controller.click(btn, 2 + self._doubleclick_counter)
                self._doubleclick_counter = 0 if self._doubleclick_counter == 2 else 2
                self._pressed = False
            else:
                self._controller.press(btn)
                self._doubleclick_counter = 0
                self._pressed = True

            self._last_press_time = current_time

    def _scroll(self, dx: int | float, dy: int | float):
        """
        Perform a mouse scroll action.
        """
        try:
            dx = int(dx)
            dy = int(dy)
        except ValueError:
            return

        self._controller.scroll(dx, dy)
