#  Perpetua - open-source and cross-platform KVM software.
#  Copyright (c) 2026 Federico Izzi.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

import asyncio
from typing import Optional

from event import (
    EventType,
    EventMapper,
    KeyboardEvent,
    ActiveScreenChangedEvent,
    ClientConnectedEvent,
    ClientDisconnectedEvent,
    ClientActiveEvent,
)
from event.bus import EventBus

from pynput.keyboard import (
    Key,
    KeyCode,
    Listener as KeyboardListener,
    Controller as KeyboardController,
)
# import keyboard as hotkey_controller  # Unused anymore

from network.stream.handler import StreamHandler

from utils.logging import get_logger
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

        # Check if it's a vk_ key
        if key.startswith("vk_"):
            try:
                vk_code = int(key[3:])
                return KeyCode.from_vk(vk_code)
            except ValueError:
                pass

        # Next check if it's a single character (KeyCode)
        try:
            return KeyCode.from_char(key)
        except Exception:
            pass

        # Otherwise return the original string (unmapped)
        return None

    @staticmethod
    def is_special(
        key: Key | KeyCode | None, filter_out: Optional[list[Key]] = None
    ) -> bool:
        """
        Check if the given key is a special key (pynput Key) or a character key (KeyCode).
        Args:
            key (Key | KeyCode | None): The key to check.
            filter_out (Optional[list[Key]]): List of keys to filter out from being considered special.
        Returns:
            bool: True if the key is a special key and not in filter_out, False otherwise
        """
        if filter_out and key in filter_out:
            return False

        return isinstance(key, Key)


class ServerKeyboardListener(object):
    """
    Base class for server-side keyboard listeners.
    """

    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
        filtering: bool = True,
    ):
        """
        Initializes the server keyboard listener.

        Args:
            event_bus (EventBus): The event bus for inter-component communication.
            stream_handler (StreamHandler): The stream handler for sending keyboard events.
            command_stream (StreamHandler): The command stream handler.
            filtering (bool): Whether to apply platform-specific filtering.
        """

        self.stream = stream_handler  # Should be a keyboard stream
        self.command_stream = command_stream
        self.event_bus = event_bus

        self._listening = False
        self._active_screens = {}
        self._screen_size: tuple[int, int] = Screen.get_size()

        # Check platform to set appropriate mouse filter
        self._filter_args = {}
        if filtering:
            try:
                import platform

                current_platform = platform.system()
                if current_platform == "Darwin":
                    self._filter_args["darwin_intercept"] = self._darwin_suppress_filter
                elif current_platform == "Windows":
                    self._filter_args["win32_event_filter"] = (
                        self._win32_suppress_filter
                    )
            except Exception:
                pass

        self._listener: Optional[KeyboardListener] = None

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
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_CONNECTED, callback=self._on_client_connected
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected,
        )

    def _create_listener(self) -> KeyboardListener:
        """
        Creates a new keyboard listener instance.
        """
        return KeyboardListener(
            on_press=self.on_press, on_release=self.on_release, **self._filter_args
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
                    "No event loop running when starting keyboard listener. Async operations may fail."
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
        if self._listener and self.is_alive():
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
        self._active_screens[client_screen] = True
        await asyncio.sleep(0)

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

        await asyncio.sleep(0)

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        """
        Async event handler for when the active screen changes.
        """
        if data is None:
            return

        # If active screen is not none then we can start listening to mouse events
        active_screen = data.active_screen

        if active_screen is not None:
            self._listening = True
        else:
            self._listening = False

        await asyncio.sleep(0)

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
                        "Event loop not running, cannot schedule async operation"
                    )
            except Exception as e:
                self._logger.warning(
                    f"No event loop available for async operation -> {e}"
                )

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
            event = KeyboardEvent(
                key=self._get_key(key), action=KeyboardEvent.PRESS_ACTION
            )
            self._schedule_async(self.stream.send(event))
        except Exception as e:
            self._logger.error(f"Error handling key press -> {e}")

    def on_release(self, key: Key | KeyCode | None):
        """
        Callback for key release events.
        """
        if not self._listening or key is None:
            return

        try:
            event = KeyboardEvent(
                key=self._get_key(key), action=KeyboardEvent.RELEASE_ACTION
            )
            self._schedule_async(self.stream.send(event))
        except Exception as e:
            self._logger.error(f"Error handling key release -> {e}")


class ClientKeyboardController(object):
    """
    Base class for client-side keyboard controllers.
    """

    # Keys that can be hold pressed
    _SPECIAL_KEYS_FILTER: list[Key] = [
        Key.space,
        Key.shift,
        Key.shift_l,
        Key.shift_r,
        Key.ctrl,
        Key.ctrl_l,
        Key.ctrl_r,
        Key.alt,
        Key.alt_l,
        Key.alt_r,
        Key.backspace,
        Key.enter,
        Key.esc,
        Key.tab,
        Key.up,
        Key.down,
        Key.left,
        Key.right,
        Key.media_volume_up,
        Key.media_volume_down,
        # Key.delete, TODO: Check
        Key.end,
        Key.page_up,
        Key.page_down,
    ]

    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
    ):
        """
        Initializes the client keyboard controller.

        Args:
            event_bus (EventBus): The event bus for inter-component communication.
            stream_handler (StreamHandler): The stream handler for receiving keyboard events.
            command_stream (StreamHandler): The command stream handler.
        """
        self.stream = stream_handler  # Should be a mouse stream
        self.command_stream = command_stream  # Should be a command stream
        self.event_bus = event_bus
        self._cross_screen_event = asyncio.Event()

        self._is_active = False

        self._controller = KeyboardController()
        # self._hotkey_controller = hotkey_controller
        self._pressed = False
        # Track pressed keys for hotkey combinations
        self.pressed_keys = set()
        self._pressed_general_keys = set()
        self.is_caps_locked = False

        self._logger = get_logger(self.__class__.__name__)

        # Async queue instead of multiprocessing queue
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

        # Register to receive mouse events from the stream (async callback)
        self.stream.register_receive_callback(
            self._key_event_callback, message_type="keyboard"
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
                finally:
                    await asyncio.sleep(0)

            # Start worker task
            self._worker_task = asyncio.create_task(self._run_worker())
            self._logger.debug("Async worker started.")
            await asyncio.sleep(0)

    async def stop(self):
        """
        Stops the async mouse controller worker task.
        """
        if self._running:
            self._running = False

            # Clear pressed keys
            await self._clear_pressed_keys()

            if self._worker_task:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
                self._worker_task = None

            self._logger.debug("Async worker stopped.")

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
        loop = asyncio.get_running_loop()

        while self._running:
            try:
                # Get message from async queue
                message = await self._queue.get()

                event = EventMapper.get_event(message)
                if not isinstance(event, KeyboardEvent):
                    continue

                await loop.run_in_executor(None, self._key_event_action, event)
                await asyncio.sleep(0)
            except asyncio.TimeoutError:
                await asyncio.sleep(0)
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Error in worker -> {e}")
                await asyncio.sleep(0.01)

    async def _on_client_active(self, data: Optional[ClientActiveEvent]):
        """
        Async event handler for when client becomes active.
        """
        self._is_active = True
        self._cross_screen_event.clear()

        # Auto-start if not running
        if not self._running:
            await self.start()
        await asyncio.sleep(0)

    async def _on_client_inactive(self, data: Optional[ClientActiveEvent]):
        """
        Async event handler for when a client becomes inactive.
        """
        self._is_active = False
        await self._clear_pressed_keys()
        await asyncio.sleep(0)

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
            self._logger.error(f"Failed to process mouse event -> {e}")
            await asyncio.sleep(0)

    async def _clear_pressed_keys(self):
        """
        Helper to release all currently pressed keys.
        """
        for key in list(self.pressed_keys):
            try:
                self._controller.release(key)
            except Exception:
                pass
            await asyncio.sleep(0)

        for key in list(self._pressed_general_keys):
            try:
                self._controller.release(key)
            except Exception:
                pass
            await asyncio.sleep(0)
        self.pressed_keys.clear()
        self._pressed_general_keys.clear()

    def _key_event_action(self, event: KeyboardEvent):
        """
        Synchronous action to perform key event.
        Os-specific implementations should override this method.
        """
        key = KeyUtilities.map_key(event.key)
        if key is None:
            self._logger.warning(f"Unmapped key received: {event.key}")
            return

        if event.action == KeyboardEvent.PRESS_ACTION:
            # Handle Caps Lock toggle
            if key == Key.caps_lock:
                if self.is_caps_locked:
                    self._controller.release(key)
                else:
                    self._controller.press(key)
                self.is_caps_locked = not self.is_caps_locked
            elif KeyUtilities.is_special(
                key, filter_out=self._SPECIAL_KEYS_FILTER
            ):  # General special key handling
                if key not in self.pressed_keys:
                    self.pressed_keys.add(key)
                    self._controller.press(key)
            else:
                self._controller.press(key)
                self._pressed_general_keys.add(key)
        elif event.action == KeyboardEvent.RELEASE_ACTION:
            if key == Key.caps_lock:
                if self.is_caps_locked:
                    self._controller.release(key)
                else:
                    self._controller.press(key)
                self.is_caps_locked = not self.is_caps_locked
            elif KeyUtilities.is_special(
                key, filter_out=self._SPECIAL_KEYS_FILTER
            ):  # General special key handling
                if key in self.pressed_keys:
                    self.pressed_keys.discard(key)
                    self._controller.release(key)
            else:
                self._controller.release(key)
                self._pressed_general_keys.discard(key)
