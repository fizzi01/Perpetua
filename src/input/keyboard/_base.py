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
import os
import signal
from typing import Any, Optional

from event import (
    BusEventType,
    EventMapper,
    KeyboardEvent,
    ActiveScreenChangedEvent,
    ClientConnectedEvent,
    ClientDisconnectedEvent,
    ClientActiveEvent,
    CommandEvent,
    KeyboardStateSyncCommandEvent,
    CrossScreenCommandEvent,
    ForceScreenChangeCommandEvent,
)
from event.bus import EventBus

from network.stream.handler import StreamHandler
from network.protocol.message import MessageType

from utils.logging import get_logger
from utils.screen import Screen

from input.utils import KeyUtilities, ScreenEdge
from .backend import KeyboardListener, Key, KeyCode, HotKey, KeyboardController, BACKEND


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
        mouse_listener: Optional[Any] = None,
    ):
        """
        Initializes the server keyboard listener.

        Args:
            event_bus (EventBus): The event bus for inter-component communication.
            stream_handler (StreamHandler): The stream handler for sending keyboard events.
            command_stream (StreamHandler): The command stream handler.
            filtering (bool): Whether to apply platform-specific filtering.
            mouse_listener: Reference to the :class:`ServerMouseListener` so
                the directional hotkeys can resolve targets spatially
                (``resolve_neighbour``) and the cycling hotkey can read
                the active-clients set. Optional — when ``None`` the
                hotkeys gracefully no-op.
        """

        self.stream = stream_handler  # Should be a keyboard stream
        self.command_stream = command_stream
        self.event_bus = event_bus
        self._mouse_listener = mouse_listener
        # Insertion-ordered index into ``_active_clients`` used by the
        # Tab/Shift+Tab cycling hotkey to remember where we are.
        self._hotkey_cycle_index: int = -1

        self._listening = False
        # Presence map of active client UIDs (see migration to UID-keyed
        # routing in src/input/mouse/_base.py for the equivalent dict
        # on the mouse listener).
        self._active_clients: dict[str, bool] = {}
        self._screen_size: tuple[int, int] = Screen.get_size()
        self._caps_lock_state = self._get_lock_state()

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
                elif current_platform == "Linux":
                    self._filter_args["xorg_filter"] = self._xorg_suppress_filter
            except Exception:
                pass

        self._listener: Optional[KeyboardListener] = None

        # Serializes concurrent invocations of `_sync_caps_lock_state`.
        self._caps_lock_sync_lock = asyncio.Lock()

        self._hotkey_consumed = False
        self._hotkeys: list[HotKey] = self._build_hotkeys()

        self._logger = get_logger(self.__class__.__name__)

        self._logger.info(
            f"Keyboard listener backend: {BACKEND.get('keyboard_listener', 'unknown')}"
        )

        # Store event loop reference for thread-safe async scheduling
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running yet - will be set when start() is called
            self._loop = None

        # TODO: Work in progress
        # self.command_stream.register_receive_callback(
        #     self._command_callback, message_type=MessageType.COMMAND
        # )

        # Subscribe with async callbacks
        self.event_bus.subscribe(
            event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
            callback=self._on_active_screen_changed,
        )
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_CONNECTED, callback=self._on_client_connected
        )
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected,
        )

    @staticmethod
    def _get_lock_state() -> bool:
        """
        Helper to get current Caps Lock state.
        Os-specific implementations should override this method to return actual state.
        """
        return False

    def _create_listener(self) -> KeyboardListener:
        """
        Creates a new keyboard listener instance.
        """
        return KeyboardListener(
            on_press=self.on_press, on_release=self.on_release, **self._filter_args
        )

    def start(self) -> bool:
        """
        Starts the keyboard listener.
        """
        # Always re-capture the running loop: a previous start() may have
        # cached a loop that has since been closed (e.g. between tests).
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

    def _build_hotkeys(self) -> list[HotKey]:
        """
        Build HotKey instances for all registered combinations.
        Uses Ctrl+Shift+P+<action> to avoid Option/Alt issues on macOS.
        """

        def make_cb(coro_fn, *args):
            def cb():
                self._hotkey_consumed = True
                self._schedule_async(coro_fn(*args))

            return cb

        entries = [
            (
                "<ctrl>+<shift>+p+<left>",
                make_cb(self._hotkey_switch_direction, ScreenEdge.LEFT),
            ),
            (
                "<ctrl>+<shift>+p+<right>",
                make_cb(self._hotkey_switch_direction, ScreenEdge.RIGHT),
            ),
            (
                "<ctrl>+<shift>+p+<up>",
                make_cb(self._hotkey_switch_direction, ScreenEdge.TOP),
            ),
            (
                "<ctrl>+<shift>+p+<down>",
                make_cb(self._hotkey_switch_direction, ScreenEdge.BOTTOM),
            ),
            # Cycle through every active client regardless of layout —
            # useful when clients sit in disjoint locations the
            # directional hotkeys can't reach from the current cursor.
            ("<ctrl>+<shift>+p+<tab>", make_cb(self._hotkey_cycle_client, 1)),
            ("<ctrl>+<shift>+p+<esc>", make_cb(self._hotkey_switch_to_server)),
            ("<ctrl>+<shift>+q", make_cb(self._hotkey_panic)),
        ]
        return [HotKey(HotKey.parse(combo), cb) for combo, cb in entries]

    # Modifier normalization map
    _MOD_MAP: dict = {
        Key.ctrl_l: Key.ctrl,
        Key.ctrl_r: Key.ctrl,
        Key.shift_l: Key.shift,
        Key.shift_r: Key.shift,
        Key.alt_l: Key.alt,
        Key.alt_r: Key.alt,
        Key.cmd_l: Key.cmd,
        Key.cmd_r: Key.cmd,
    }

    def _canonical(self, key: Key | KeyCode) -> Key | KeyCode:
        """
        Normalize key to canonical form for HotKey matching.
        Delegates to the pynput Listener's canonical() when available,
        otherwise falls back to manual normalization.
        """
        if self._listener is not None and hasattr(self._listener, "canonical"):
            return self._listener.canonical(key)  # ty:ignore[call-non-callable]
        # Fallback
        if isinstance(key, Key):
            if key in self._MOD_MAP:
                return self._MOD_MAP[key]
            try:
                return KeyCode.from_vk(key.value.vk)
            except Exception:
                pass
        return key

    LISTENER_JOIN_TIMEOUT = 2.0  # sec

    def stop(self) -> bool:
        """
        Stops the keyboard listener. ``pynput.Listener.stop`` returns
        immediately; we explicitly join the OS-level listener thread.
        """
        if self._listener and self.is_alive():
            self._listener.stop()
            try:
                self._listener.join(timeout=self.LISTENER_JOIN_TIMEOUT)
            except RuntimeError:
                pass
            if self._listener.is_alive():
                self._logger.warning(
                    "Keyboard listener thread still alive after "
                    f"{self.LISTENER_JOIN_TIMEOUT}s - proceeding without join"
                )

        self._logger.debug("Stopped.")
        return True

    def is_alive(self):
        return self._listener.is_alive() if self._listener else False

    async def _command_callback(self, message):
        try:
            event = EventMapper.get_event(message)
            if not isinstance(event, CommandEvent):
                self._logger.warning(f"Received non-command event -> {event}")
                return

            if event.command == CommandEvent.KEYBOARD_STATE_SYNC:
                kev = KeyboardStateSyncCommandEvent().from_command_event(event)
                for key in kev.get_pressed_keys():
                    key = KeyUtilities.map_key(key)
                    if key is not None and key == Key.caps_lock:
                        await self._sync_caps_lock_state(ext_state=True)
                        break
        except Exception as e:
            self._logger.error(f"Error processing command message ({e})")
            return

    async def _on_client_connected(self, data: Optional[ClientConnectedEvent]):
        """
        Async event handler for when a client connects.
        """
        if data is None:
            return

        client_uid = data.client_uid
        if client_uid:
            self._active_clients[client_uid] = True
        await asyncio.sleep(0)

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        """
        Async event handler for when a client disconnects.
        """
        if data is None:
            return

        client_uid = data.client_uid
        if client_uid and client_uid in self._active_clients:
            del self._active_clients[client_uid]

        # if no clients are active anymore, stop listening
        if not self._active_clients:
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
            asyncio.create_task(self._sync_caps_lock_state())
        else:
            self._listening = False

        await asyncio.sleep(0)

    async def _sync_caps_lock_state(self, ext_state: Optional[bool] = None):
        """
        Sync server's Caps Lock state with clients by sending a toggle event if needed.

        Wrapped in ``_caps_lock_sync_lock`` so two rapidly-fired active-screen
        changes cannot interleave the read/compare/send sequence and emit
        spurious double-toggles.
        """
        async with self._caps_lock_sync_lock:
            if ext_state is None:
                ext_state = self._get_lock_state()

            if self._get_lock_state() != ext_state:
                event = KeyboardEvent(
                    key=Key.caps_lock.name, action=KeyboardEvent.PRESS_ACTION
                )
                await self.stream.send(event)

    def _darwin_suppress_filter(self, event_type, event):
        raise NotImplementedError("Mouse suppress filter not implemented yet.")

    def _win32_suppress_filter(self, msg, data):
        raise NotImplementedError("Mouse suppress filter not implemented yet.")

    def _xorg_suppress_filter(self, event):
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
                self._logger.error(f"Error scheduling coroutine ({e})")

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
                    f"No event loop available for async operation ({e})"
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
        if key is None:
            return

        self._hotkey_consumed = False
        canonical = self._canonical(key)
        for hotkey in self._hotkeys:
            hotkey.press(canonical)
        if self._hotkey_consumed:
            return  # Hotkey consumed – do not forward to client

        if not self._listening:
            return

        try:
            event = KeyboardEvent(
                key=self._get_key(key), action=KeyboardEvent.PRESS_ACTION
            )
            self._schedule_async(self.stream.send(event))
        except Exception as e:
            self._logger.error(f"Error handling key press ({e})")

    def on_release(self, key: Key | KeyCode | None):
        """
        Callback for key release events.
        """
        if key is None:
            return

        canonical = self._canonical(key)
        for hotkey in self._hotkeys:
            hotkey.release(canonical)

        if not self._listening:
            return

        try:
            event = KeyboardEvent(
                key=self._get_key(key), action=KeyboardEvent.RELEASE_ACTION
            )
            self._schedule_async(self.stream.send(event))
        except Exception as e:
            self._logger.error(f"Error handling key release ({e})")

    def _query_cursor_position(self) -> Optional[tuple[float, float]]:
        """Best-effort read of the current cursor position for the
        spatial hotkey resolver. Returns ``None`` if pynput can't be
        loaded (e.g. headless test).
        """
        try:
            from input.mouse.backend import MouseController

            ctl = MouseController()
            pos = ctl.position
            if pos is None or len(pos) != 2:
                return None
            return float(pos[0]), float(pos[1])
        except Exception:
            return None

    async def _hotkey_switch_direction(self, edge: ScreenEdge) -> None:
        """Directional hotkey: resolve the client UID that lives off
        ``edge`` of the server monitor currently under the cursor (via
        the mouse listener's spatial topology) and switch focus to it.

        Combination: ``Ctrl+Shift+P+<Arrow>``. No-op when the mouse
        listener isn't available, the cursor can't be queried, or no
        client owns that direction at this position — preferable to
        the old behaviour of routing to a fixed legacy ScreenPosition
        regardless of layout.
        """
        if self._mouse_listener is None:
            self._logger.debug("Hotkey direction ignored: no mouse listener wired.")
            return
        cursor = self._query_cursor_position()
        if cursor is None:
            self._logger.debug("Hotkey direction ignored: cursor position unavailable.")
            return
        client_uid = self._mouse_listener.resolve_neighbour(edge, cursor[0], cursor[1])
        if not client_uid:
            self._logger.debug(
                f"Hotkey direction {edge.name}: no spatial neighbour at cursor."
            )
            return
        await self._hotkey_switch_screen(client_uid)

    async def _hotkey_cycle_client(self, direction: int) -> None:
        """Cycle the active client forward (``direction=1``) or
        backward (``direction=-1``) through the mouse listener's
        ``_active_clients`` insertion order.

        Combination: ``Ctrl+Shift+P+Tab`` / ``Shift+Tab``. Wraps at
        the boundaries and stays a no-op when no clients are connected.
        Used when the topology is too disjoint for the directional
        hotkeys to reach a client from the current cursor position.
        """
        if self._mouse_listener is not None:
            uids = self._mouse_listener.get_active_client_uids()
        else:
            uids = list(self._active_clients.keys())
        if not uids:
            return
        self._hotkey_cycle_index = (self._hotkey_cycle_index + direction) % len(uids)
        await self._hotkey_switch_screen(uids[self._hotkey_cycle_index])

    async def _hotkey_switch_screen(self, client_uid: str) -> None:
        """
        Hotkey handler: switch input focus to the given client by UID.
        Only acts if the target client is currently connected and active.
        Combination: Ctrl+Shift+P+<Arrow>. The direction-to-UID
        resolution is performed by the caller via the spatial topology
        (see ``ServerMouseListener.resolve_neighbour``).
        """
        if not client_uid or client_uid not in self._active_clients:
            self._logger.debug(
                f"Hotkey switch ignored: client '{client_uid}' not active."
            )
            return

        try:
            await self.event_bus.dispatch(
                event_type=BusEventType.SCREEN_CHANGE_GUARD,
                data=ActiveScreenChangedEvent(active_screen=client_uid),
            )
            await self.command_stream.send(CrossScreenCommandEvent(target=client_uid))
        except Exception as e:
            self._logger.error(f"Error during hotkey screen switch ({e})")

    async def _hotkey_switch_to_server(self) -> None:
        """
        Hotkey handler: release any active client and return focus to the server.
        Combination: Ctrl+Shift+P+Esc
        """
        if not self._listening:
            self._logger.debug("Hotkey switch-to-server ignored: already on server.")
            return

        try:
            await self.event_bus.dispatch(
                event_type=BusEventType.SCREEN_CHANGE_GUARD,
                data=ActiveScreenChangedEvent(active_screen=None),
            )
            # Notify clients of forced screen change to server
            await self.command_stream.send(ForceScreenChangeCommandEvent())
        except Exception as e:
            self._logger.error(f"Error during hotkey switch-to-server ({e})")

    async def _hotkey_panic(self) -> None:
        """
        Hotkey handler: panic button - sends SIGQUIT to the current process.
        Combination: Ctrl+Shift+Q
        """
        self._logger.warning("Panic hotkey triggered: sending SIGTERM to self.")
        os.kill(os.getpid(), signal.SIGTERM)
        # Are we still alive? If so, try SIGKILL
        await asyncio.sleep(1)
        if self.is_alive():
            self._logger.warning("Process still alive after SIGTERM, sending SIGKILL.")
            os.kill(os.getpid(), signal.SIGKILL)


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
        self._caps_lock_state = False

        self._logger = get_logger(self.__class__.__name__)

        self._logger.info(
            f"Keyboard controller backend: {BACKEND.get('keyboard_controller', 'unknown')}"
        )

        # Async queue instead of multiprocessing queue
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

        # Register to receive mouse events from the stream (async callback)
        self.stream.register_receive_callback(
            self._key_event_callback, message_type=MessageType.KEYBOARD
        )

        # Subscribe with async callbacks
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_ACTIVE, callback=self._on_client_active
        )
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_INACTIVE, callback=self._on_client_inactive
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
                self._logger.error(f"Error in worker ({e})")
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
            self._logger.error(f"Failed to process mouse event ({e})")
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
                if self._caps_lock_state:
                    self._controller.release(key)
                else:
                    self._controller.press(key)
                self._caps_lock_state = not self._caps_lock_state
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
                if self._caps_lock_state:
                    self._controller.release(key)
                else:
                    self._controller.press(key)
                self._caps_lock_state = not self._caps_lock_state
            elif KeyUtilities.is_special(
                key, filter_out=self._SPECIAL_KEYS_FILTER
            ):  # General special key handling
                if key in self.pressed_keys:
                    self.pressed_keys.discard(key)
                    self._controller.release(key)
            else:
                self._controller.release(key)
                self._pressed_general_keys.discard(key)
