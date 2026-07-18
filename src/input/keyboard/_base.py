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
from typing import Optional

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
    ForceScreenChangeCommandEvent,
    ScreenSwitchDirectionalRequestEvent,
    ScreenSwitchCycleRequestEvent,
)
from event.bus import EventBus

from network.stream.handler import StreamHandler
from network.protocol.message import MessageType

from utils.logging import get_logger
from utils.screen import Screen

from input.utils import KeyUtilities, ScreenEdge
from .backend import KeyboardListener, Key, KeyCode, HotKey, KeyboardController, BACKEND


class ServerKeyboardListener(object):
    """Base class for server-side keyboard listeners."""

    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
        filtering: bool = True,
    ):
        self.stream = stream_handler  # Should be a keyboard stream
        self.command_stream = command_stream
        self.event_bus = event_bus

        self._listening = False
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
            "keyboard listener backend selected",
            backend=BACKEND.get("keyboard_listener", "unknown"),
        )

        # Store event loop reference for thread-safe async scheduling
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running yet - will be set when start() is called
            self._loop = None

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
        """Current Caps Lock state. OS-specific subclasses override."""
        return False

    def _create_listener(self) -> KeyboardListener:
        return KeyboardListener(
            on_press=self.on_press, on_release=self.on_release, **self._filter_args
        )

    def start(self) -> bool:
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
        """Build HotKey instances. Uses Ctrl+Shift+P+<action> to avoid Option/Alt issues on macOS."""

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
            # Cycle through every active client regardless of layout -
            # useful when clients sit in disjoint locations the
            # directional hotkeys can't reach from the current cursor.
            ("<ctrl>+<shift>+p+<tab>+1", make_cb(self._hotkey_cycle_client, 1)),
            ("<ctrl>+<shift>+p+<tab>+2", make_cb(self._hotkey_cycle_client, -1)),
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
        """Normalize key for HotKey matching, delegating to pynput when available."""
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
        # pynput.Listener.stop returns immediately; join the OS thread.
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
                self._logger.warning("Received non-command event", event=repr(event))
                return

            if event.command == CommandEvent.KEYBOARD_STATE_SYNC:
                kev = KeyboardStateSyncCommandEvent().from_command_event(event)
                for key in kev.get_pressed_keys():
                    key = KeyUtilities.map_key(key)
                    if key is not None and key == Key.caps_lock:
                        await self._sync_caps_lock_state(ext_state=True)
                        break
        except Exception as e:
            self._logger.error("Error processing command message", error=str(e))
            return

    async def _on_client_connected(self, data: Optional[ClientConnectedEvent]):
        if data is None:
            return

        client_uid = data.client_uid
        if client_uid:
            self._active_clients[client_uid] = True
        await asyncio.sleep(0)

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        if data is None:
            return

        client_uid = data.client_uid
        if client_uid and client_uid in self._active_clients:
            del self._active_clients[client_uid]

        if not self._active_clients:
            self._listening = False

        await asyncio.sleep(0)

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        if data is None:
            return

        active_screen = data.active_screen

        if active_screen is not None:
            self._listening = True
            asyncio.create_task(self._sync_caps_lock_state())
        else:
            self._listening = False

        await asyncio.sleep(0)

    async def _sync_caps_lock_state(self, ext_state: Optional[bool] = None):
        """Sync server's Caps Lock state with clients by sending a toggle when needed.
        Wrapped in _caps_lock_sync_lock so rapid active-screen changes can't double-toggle."""
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
        """Schedule async coroutine from sync context (pynput thread)."""
        if self._loop is not None and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(coro, self._loop)
                return
            except Exception as e:
                self._logger.error("Error scheduling coroutine", error=str(e))

        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
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
        if isinstance(key, KeyCode):
            return key.char if key.char is not None else f"vk_{key.vk}"
        elif isinstance(key, Key):
            return key.name if key.name is not None else f"vk_{key.value.vk}"

        raise AttributeError(f"Key {key} is not a valid key.")

    def on_press(self, key: Key | KeyCode | None):
        if key is None:
            return

        self._hotkey_consumed = False
        canonical = self._canonical(key)
        for hotkey in self._hotkeys:
            hotkey.press(canonical)
        if self._hotkey_consumed:
            return

        if not self._listening:
            return

        try:
            event = KeyboardEvent(
                key=self._get_key(key), action=KeyboardEvent.PRESS_ACTION
            )
            self._schedule_async(self.stream.send(event))
        except Exception as e:
            self._logger.error("Error handling key press", error=str(e))

    def on_release(self, key: Key | KeyCode | None):
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
            self._logger.error("Error handling key release", error=str(e))

    async def _hotkey_switch_direction(self, edge: ScreenEdge) -> None:
        """Ctrl+Shift+P+<Arrow>: spatial client switch. The mouse listener resolves the target."""
        try:
            await self.event_bus.dispatch(
                event_type=BusEventType.SCREEN_SWITCH_DIRECTIONAL_REQUEST,
                data=ScreenSwitchDirectionalRequestEvent(edge=edge),
            )
        except Exception as e:
            self._logger.error("Error dispatching directional hotkey", error=str(e))

    async def _hotkey_cycle_client(self, direction: int) -> None:
        """Ctrl+Shift+P+Tab / Shift+Tab: cycle active client forward (1) or back (-1).
        Used when topology is too disjoint for directional hotkeys."""
        try:
            self._logger.debug("Hotkey cycle client", direction=direction)
            await self.event_bus.dispatch(
                event_type=BusEventType.SCREEN_SWITCH_CYCLE_REQUEST,
                data=ScreenSwitchCycleRequestEvent(direction=direction),
            )
        except Exception as e:
            self._logger.error("Error dispatching cycle hotkey", error=str(e))

    async def _hotkey_switch_to_server(self) -> None:
        """Ctrl+Shift+P+Esc: release any active client and return focus to the server."""
        if not self._listening:
            self._logger.debug("Hotkey switch-to-server ignored: already on server.")
            return

        try:
            await self.event_bus.dispatch(
                event_type=BusEventType.SCREEN_CHANGE_GUARD,
                data=ActiveScreenChangedEvent(active_screen=None),
            )
            await self.command_stream.send(ForceScreenChangeCommandEvent())
        except Exception as e:
            self._logger.error("Error during hotkey switch-to-server", error=str(e))

    async def _hotkey_panic(self) -> None:
        """Ctrl+Shift+Q: panic button - SIGTERM then SIGKILL self.

        The SIGKILL fallback runs in a separate task so this handler
        returns immediately, letting SIGTERM propagate without
        blocking the keyboard listener loop for a full second.
        """
        self._logger.warning("Panic hotkey triggered: sending SIGTERM to self.")
        os.kill(os.getpid(), signal.SIGTERM)

        async def _fallback_kill() -> None:
            await asyncio.sleep(1)
            if self.is_alive():
                self._logger.warning(
                    "Process still alive after SIGTERM, sending SIGKILL."
                )
                os.kill(os.getpid(), signal.SIGKILL)

        asyncio.ensure_future(_fallback_kill())


class ClientKeyboardController(object):
    """Base class for client-side keyboard controllers."""

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
        self.stream = stream_handler
        self.command_stream = command_stream
        self.event_bus = event_bus
        self._cross_screen_event = asyncio.Event()

        self._is_active = False

        self._controller = KeyboardController()
        self._pressed = False
        self.pressed_keys = set()
        self._pressed_general_keys = set()
        self._caps_lock_state = False

        self._logger = get_logger(self.__class__.__name__)

        self._logger.info(
            "keyboard controller backend selected",
            extra={"backend": BACKEND.get("keyboard_controller", "unknown")},
        )

        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

        self.stream.register_receive_callback(
            self._key_event_callback, message_type=MessageType.KEYBOARD
        )

        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_ACTIVE, callback=self._on_client_active
        )
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_INACTIVE, callback=self._on_client_inactive
        )

    async def start(self):
        if not self._running:
            self._running = True
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                finally:
                    await asyncio.sleep(0)

            self._worker_task = asyncio.create_task(self._run_worker())
            self._logger.debug("Async worker started.")
            await asyncio.sleep(0)

    async def stop(self):
        if self._running:
            self._running = False

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
        return (
            self._running
            and self._worker_task is not None
            and not self._worker_task.done()
        )

    async def _run_worker(self):
        loop = asyncio.get_running_loop()

        while self._running:
            try:
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
                self._logger.error("Error in worker", error=str(e))
                await asyncio.sleep(0.01)

    async def _on_client_active(self, data: Optional[ClientActiveEvent]):
        self._is_active = True
        self._cross_screen_event.clear()

        if not self._running:
            await self.start()
        await asyncio.sleep(0)

    async def _on_client_inactive(self, data: Optional[ClientActiveEvent]):
        self._is_active = False
        await self._clear_pressed_keys()
        await asyncio.sleep(0)

    async def _key_event_callback(self, message):
        try:
            if not self._running:
                await self.start()

            await self._queue.put(message)
        except Exception as e:
            self._logger.error("Failed to process mouse event", error=str(e))
            await asyncio.sleep(0)

    async def _clear_pressed_keys(self):
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
        """Apply key event. OS-specific subclasses override."""
        key = KeyUtilities.map_key(event.key)
        if key is None:
            self._logger.warning("Unmapped key received", key=event.key)
            return

        if event.action == KeyboardEvent.PRESS_ACTION:
            if key == Key.caps_lock:
                if self._caps_lock_state:
                    self._controller.release(key)
                else:
                    self._controller.press(key)
                self._caps_lock_state = not self._caps_lock_state
            elif KeyUtilities.is_special(key, filter_out=self._SPECIAL_KEYS_FILTER):
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
            elif KeyUtilities.is_special(key, filter_out=self._SPECIAL_KEYS_FILTER):
                if key in self.pressed_keys:
                    self.pressed_keys.discard(key)
                    self._controller.release(key)
            else:
                self._controller.release(key)
                self._pressed_general_keys.discard(key)
