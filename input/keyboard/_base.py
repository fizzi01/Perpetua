import asyncio
from abc import ABC
from typing import Optional

from event import EventType, EventMapper, KeyboardEvent
from event.EventBus import EventBus

from pynput.keyboard import (Key, KeyCode,
                             Listener as KeyboardListener,
                             Controller as KeyboardController)
import keyboard as hotkey_controller

from network.stream.GenericStream import StreamHandler

from utils.logging import Logger
from utils.screen import Screen

class KeyUtilities:
    """
    This class provides utility functions for keyboard key conversions.
    Like mapping key names from different OS into a specific os.
    """

    @staticmethod
    def map_key(key: str) -> Key | KeyCode | None:
        """
        For pynpuy Key are all special keys, and KeyCode are all character keys.
        """
        # First check if key is a special key in pynput
        try:
            special = Key[key]
            return special
        except KeyError:
            pass

        # Next check if it's a single character (KeyCode)
        try:
            return KeyCode.from_char(key)
        except Exception:
            pass

        # Otherwise return the original string (unmapped)
        return None

    @staticmethod
    def is_special(key: Key | KeyCode | None) -> bool:
        """
        Check if the given key is a special key (pynput Key) or a character key (KeyCode).
        """
        return isinstance(key, Key)

class BaseServerKeyboardListener(ABC):
    """
    Base class for server-side keyboard listeners.
    """
    def __init__(self, event_bus: EventBus, stream_handler: StreamHandler, command_stream: StreamHandler, filtering: bool = True):

        self.stream = stream_handler    # Should be a keyboard stream
        self.command_stream = command_stream
        self.event_bus = event_bus

        self._listening = False
        self._active_screens = {}
        self._screen_size: tuple[int,int] = Screen.get_size()

        # Check platform to set appropriate mouse filter
        self._filter_args = {}
        if filtering:
            try:
                import platform
                current_platform = platform.system()
                if current_platform == "Darwin":
                    self._filter_args["darwin_intercept"] = self._darwin_suppress_filter
                elif current_platform == "Windows":
                    self._filter_args["win32_event_filter"] = self._win32_suppress_filter
            except Exception:
                pass


        self._listener = KeyboardListener(on_press=self.on_press, on_release=self.on_release,
                                       **self._filter_args)

        self.logger = Logger()

        # Store event loop reference for thread-safe async scheduling
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running yet - will be set when start() is called
            self._loop = None

        # Subscribe with async callbacks
        self.event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED, callback=self._on_active_screen_changed)
        self.event_bus.subscribe(event_type=EventType.CLIENT_CONNECTED, callback=self._on_client_connected)
        self.event_bus.subscribe(event_type=EventType.CLIENT_DISCONNECTED, callback=self._on_client_disconnected)

    def start(self) -> bool:
        """
        Starts the mouse listener.
        """
        # Capture event loop reference if not already set
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self.logger.log("Warning: No event loop running when starting keyboard listener. Async operations may fail.", Logger.WARNING)

        self._listener.start()
        self.logger.log("Server keyboard listener started.", Logger.DEBUG)
        return True

    def stop(self) -> bool:
        """
        Stops the mouse listener.
        """
        if self.is_alive():
            self._listener.stop()
        self.logger.log("Server mouse listener stopped.", Logger.DEBUG)
        return True

    def is_alive(self):
        return self._listener.is_alive()

    async def _on_client_connected(self, data: dict):
        """
        Async event handler for when a client connects.
        """
        client_screen = data.get("client_screen")
        self._active_screens[client_screen] = True

    async def _on_client_disconnected(self, data: dict):
        """
        Async event handler for when a client disconnects.
        """
        # try to get client from data to remove from active screens
        client = data.get("client_screen")
        if client and client in self._active_screens:
            del self._active_screens[client]

        # if active screens is empty, we stop listening
        if len(self._active_screens.items()) == 0:
            self._listening = False

    async def _on_active_screen_changed(self, data: dict):
        """
        Async event handler for when the active screen changes.
        """
        # If active screen is not none then we can start listening to mouse events
        active_screen = data.get("active_screen")

        if active_screen is not None:
            self._listening = True
        else:
            self._listening = False

    def _darwin_suppress_filter(self, event_type, event):
        raise NotImplementedError("Mouse suppress filter not implemented yet.")

    def _win32_suppress_filter(self, msg, data):
        raise NotImplementedError("Mouse suppress filter not implemented yet.")

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
                self.logger.log(f"Error scheduling coroutine: {e}", Logger.ERROR)

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
                    self.logger.log("Event loop not running - cannot schedule async operation", Logger.WARNING)
            except Exception as e:
                self.logger.log(f"No event loop available for async operation: {e}", Logger.WARNING)

    @staticmethod
    def _get_key(key: Key | KeyCode) -> str:
        """
        Helper to convert pynput Key or KeyCode to string representation.
        """
        if isinstance(key, KeyCode):
            return key.char if key.char is not None else f"vk_{key.vk}"
        elif isinstance(key, Key):
            return key.name if key.name is not None else f"vk_{key.value.vk}"

        raise AttributeError(f"Key {key} is not a valid key.")

    def on_press(self, key: Key | KeyCode | None):
        """
        Callback for key press events.
        """
        if not self._listening or key is None:
            return

        try:
            event = KeyboardEvent(key=self._get_key(key), action=KeyboardEvent.PRESS_ACTION)
            self._schedule_async(self.stream.send(event))
        except Exception as e:
            self.logger.log(f"Error handling key press -> {e}", Logger.ERROR)

    def on_release(self, key: Key | KeyCode | None):
        """
        Callback for key release events.
        """
        if not self._listening or key is None:
            return

        try:
            event = KeyboardEvent(key=self._get_key(key), action=KeyboardEvent.RELEASE_ACTION)
            self._schedule_async(self.stream.send(event))
        except Exception as e:
            self.logger.log(f"Error handling key release -> {e}", Logger.ERROR)

class BaseClientKeyboardController(ABC):
    """
    Base class for client-side keyboard controllers.
    """
    def __init__(self, event_bus: EventBus, stream_handler: StreamHandler, command_stream: StreamHandler):
        self.stream = stream_handler  # Should be a mouse stream
        self.command_stream = command_stream  # Should be a command stream
        self.event_bus = event_bus
        self._cross_screen_event = asyncio.Event()

        self._is_active = False
        self._current_screen = None

        self._controller = KeyboardController()
        self._hotkey_controller = hotkey_controller
        self._pressed = False
        # Track pressed keys for hotkey combinations
        self.pressed_keys = set()

        self.logger = Logger()

        # Async queue instead of multiprocessing queue
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

        # Register to receive mouse events from the stream (async callback)
        self.stream.register_receive_callback(self._key_event_callback, message_type="keyboard")

        # Subscribe with async callbacks
        self.event_bus.subscribe(event_type=EventType.CLIENT_ACTIVE, callback=self._on_client_active)
        self.event_bus.subscribe(event_type=EventType.CLIENT_INACTIVE, callback=self._on_client_inactive)

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
            self.logger.log("Client keyboard controller async worker started.", Logger.DEBUG)

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

            self.logger.log("Client keyboard controller async worker stopped.", Logger.DEBUG)

    async def _run_worker(self):
        """
        Async worker task to handle mouse events.
        Replaces the multiprocessing worker.
        """
        loop = asyncio.get_running_loop()

        while self._running:
            try:
                # Get message from async queue
                message = await self._queue.get()

                event = EventMapper.get_event(message)
                if not isinstance(event, KeyboardEvent):
                    continue
                    
                await loop.run_in_executor(
                    None,
                    self._key_event_action,
                    event
                )

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.log(f"Error in worker -> {e}", Logger.ERROR)
                await asyncio.sleep(0.01)

    async def _on_client_active(self, data: dict):
        """
        Async event handler for when client becomes active.
        """
        self._current_screen = data.get("screen_position", None)

        self._is_active = True
        self._cross_screen_event.clear()

        # Auto-start if not running
        if not self._running:
            await self.start()

    async def _on_client_inactive(self, data: dict):
        """
        Async event handler for when a client becomes inactive.
        """
        self._is_active = False

    async def _key_event_callback(self, message):
        """
        Async callback function to handle mouse events received from the stream.
        """
        try:
            # Auto-start if not running
            if not self._running:
                await self.start()

            # Put message in async queue
            await self._queue.put(message)
        except Exception as e:
            self.logger.log(f"ClientKeyboardController: Failed to process mouse event -> {e}", Logger.ERROR)
            
    def _key_event_action(self, event: KeyboardEvent):
        """
        Synchronous action to perform key event.
        Os-specific implementations should override this method.
        """
        raise NotImplementedError()