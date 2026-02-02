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
import enum
import os
from typing import Optional, Callable, Any
from copykitten import copy, paste, CopykittenError
import hashlib

from event import (
    ClipboardEvent,
    EventType,
    EventMapper,
    ClientConnectedEvent,
    ClientDisconnectedEvent,
    ClientActiveEvent,
)
from event.bus import EventBus
from network.stream.handler import StreamHandler

from utils.logging import get_logger


class ClipboardType(enum.Enum):
    """
    Enum for different clipboard content types.
    """

    TEXT = "text"
    URL = "url"
    FILE = "file"
    IMAGE = "image"
    EMPTY = "empty"
    ERROR = "error"


class Clipboard:
    """
    Efficient async polling mechanism to monitor clipboard changes.
    Since system APIs (especially on macOS) don't provide event-based
    clipboard monitoring, we use asyncio-based polling with content hashing
    to detect changes efficiently.

    Extensible to support multiple content types. (On MacOS to access files needs further logic)
    """

    def __init__(
        self,
        on_change: Optional[Callable[[str, ClipboardType], Any]] = None,
        poll_interval: float = 0.5,
        content_types: Optional[list[ClipboardType]] = None,
    ):
        """
        Initialize the clipboard listener.

        Args:
            on_change: Async callback called when clipboard content changes.
                       Signature: async def callback(content: str, content_type: ClipboardType)
            poll_interval: Polling interval in seconds (default: 0.5)
            content_types: List of content types to monitor (default: [ClipboardType.TEXT])
        """
        self.on_change = on_change
        self.poll_interval = poll_interval
        self.content_types = content_types or [ClipboardType.TEXT]

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_hash: Optional[str] = None
        self._last_content: Optional[str] = None

        self._logger = get_logger(self.__class__.__name__)

    @staticmethod
    def _hash_content(content: str) -> str:
        """
        Create a fast hash of the clipboard content for change detection.
        Using MD5 for speed (not security).
        """
        if not content:
            return ""
        # Ensure we're encoding to bytes properly
        content_bytes = content.encode("utf-8", errors="ignore")
        return hashlib.md5(content_bytes).hexdigest()

    @staticmethod
    def _try_get_clip_file(file: str) -> str:
        """
        Os-specific logic to get a complete file path from clipboard content.
        """
        return file

    async def _get_clipboard_content(self) -> tuple[Optional[str], ClipboardType]:
        """
        Get current clipboard content asynchronously.

        Returns:
            Tuple of (content, content_type)
        """
        try:
            # Run blocking clipboard operation in executor to avoid blocking event loop
            loop = asyncio.get_running_loop()
            try:
                content = await loop.run_in_executor(None, paste)  # type: ignore
            except CopykittenError:
                return None, ClipboardType.EMPTY
            except Exception as e:
                self._logger.warning(f"Failed to access clipboard -> {e}")
                return None, ClipboardType.ERROR

            # Determine the content type (simplified - can be extended)
            content_type = ClipboardType.TEXT
            content = await loop.run_in_executor(None, self._try_get_clip_file, content)
            if isinstance(content, str):
                # Could check for URLs, file paths, etc.
                if content.startswith(
                    ("http://", "https://")
                ):  # TODO: improve URL detection
                    content_type = ClipboardType.URL
                elif os.path.isfile(content):
                    content_type = ClipboardType.FILE
            elif content is None:
                return None, ClipboardType.EMPTY

            # Filter based on monitored types
            if content_type not in self.content_types:
                return None, ClipboardType.EMPTY

            return content, content_type

        except Exception as e:
            self._logger.error(f"Error reading clipboard -> {e}")
            return None, ClipboardType.ERROR

    async def _set_clipboard_content(self, content: str) -> bool:
        """
        Set clipboard content asynchronously.

        Args:
            content: Content to set in clipboard

        Returns:
            True if successful, False otherwise
        """
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, copy, content)

            # Update our tracking
            self._last_content = content
            self._last_hash = self._hash_content(content)

            return True
        except Exception as e:
            self._logger.error(f"Error writing to clipboard -> {e}")
            return False

    async def _poll_loop(self):
        """
        Main polling loop that checks for clipboard changes.
        """
        self._logger.debug("Polling started")

        # Get initial state
        initial_content, _ = await self._get_clipboard_content()
        if initial_content:
            self._last_hash = self._hash_content(initial_content)
            self._last_content = initial_content

        while self._running:
            try:
                # Get current clipboard content
                content, content_type = await self._get_clipboard_content()
                if content is not None:
                    # Calculate hash for efficient comparison
                    current_hash = self._hash_content(content)

                    # Check if content has changed
                    if current_hash != self._last_hash:
                        self._last_hash = current_hash
                        self._last_content = content

                        # Invoke callback if registered
                        if self.on_change:
                            try:
                                if asyncio.iscoroutinefunction(self.on_change):
                                    await self.on_change(content, content_type)
                                else:
                                    # Support sync callbacks too
                                    loop = asyncio.get_running_loop()
                                    await loop.run_in_executor(
                                        None, self.on_change, content, content_type
                                    )
                            except Exception as e:
                                self._logger.error(f"Error in callback -> {e}")

                # Sleep until next poll
                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                self._logger.debug("Clipboard polling cancelled")
                self._running = False
                break
            except Exception as e:
                self._logger.debug(f"Error in poll loop -> {e}")
                # Continue polling even on error
                await asyncio.sleep(self.poll_interval)

        self._logger.debug("Polling stopped")

    async def start(self):
        """
        Start the clipboard monitoring.
        """
        if self._running:
            self._logger.warning("Already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        self._logger.debug(f"Started monitoring (poll interval: {self.poll_interval}s)")

    async def stop(self):
        """
        Stop the clipboard monitoring.
        """
        if not self._running:
            return

        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        self._logger.debug("Stopped monitoring")

    def is_listening(self) -> bool:
        """
        Check if the clipboard listener is currently running.

        Returns:
            True if running, False otherwise
        """
        return self._running

    def get_last_content(self) -> Optional[str]:
        """
        Get the last known clipboard content without polling.

        Returns:
            Last clipboard content or None
        """
        return self._last_content

    def set_poll_interval(self, interval: float):
        """
        Update the polling interval.

        Args:
            interval: New polling interval in seconds
        """
        self.poll_interval = max(0.1, interval)  # Minimum 100ms
        self._logger.debug(f"Clipboard poll interval updated to {self.poll_interval}s")

    async def set_clipboard(self, content: str) -> bool:
        """
        Set clipboard content and update internal state.

        Args:
            content: Content to set

        Returns:
            True if successful
        """
        return await self._set_clipboard_content(content)

    async def _debug_set_clipboard(self, content: str) -> bool:
        """
        Set clipboard content WITHOUT updating internal state.
        This allows the polling loop to detect the change.

        USE ONLY FOR TESTING - this bypasses the normal state tracking.

        Args:
            content: Content to set

        Returns:
            True if successful
        """
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, copy, content)
            # Deliberately NOT updating _last_hash and _last_content
            # so the polling loop will detect this as a change
            return True
        except Exception as e:
            self._logger.error(f"Error writing to clipboard (debug) -> {e}")
            return False


class ClipboardListener:
    """
    Base clipboard listener that integrates with an event bus and stream handlers.
    Listens for clipboard changes and dispatches events to connected clients.
    """

    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
        clipboard=Clipboard,
    ):
        """
        Initialize the clipboard listener.
        Args:
            event_bus: Event bus for event handling
            stream_handler: Stream handler to send clipboard events
            command_stream: Command stream handler
            clipboard: Clipboard monitoring class (default to Os-specific Clipboard)
        """
        self.event_bus = event_bus
        self.stream_handler = (
            stream_handler  # Can be a broadcast stream handler or unidirectional
        )
        self.command_stream = command_stream

        self._active_screens = {}
        # Internal flag to track if we should be listening (When at least one client is active or connected)
        # This flag should not be set directly, but via event handlers
        self._listening = False

        self._logger = get_logger(self.__class__.__name__)

        self.clipboard = clipboard(
            on_change=self._on_clipboard_change,
            content_types=[ClipboardType.TEXT, ClipboardType.URL, ClipboardType.FILE],
        )

        self.event_bus.subscribe(
            event_type=EventType.CLIENT_CONNECTED, callback=self._on_client_connected
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected,
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_ACTIVE, callback=self._on_client_active
        )
        # Behavior change: We do not stop listening on inactive clients
        # self.event_bus.subscribe(event_type=EventType.CLIENT_INACTIVE, callback=self._on_client_inactive)

    async def start(self) -> bool:
        """
        Start the clipboard listener.
        """
        # We start the listener only when there is at least one connected client
        if (
            not self.clipboard.is_listening() and self._listening
        ):  # We resume listening if needed
            await self.clipboard.start()
        self._logger.debug("Started")
        await asyncio.sleep(0)
        return True

    async def stop(self):
        """
        Stop the clipboard listener.
        """
        if self.clipboard.is_listening():
            await self.clipboard.stop()

        self._logger.debug("Stopped")
        await asyncio.sleep(0)

    def is_alive(self) -> bool:
        """
        Check if the clipboard listener is active.
        Returns:
            True if clipboard listener is running
        """
        return self.clipboard.is_listening() and self._listening

    async def _on_client_active(self, data: Optional[ClientActiveEvent]):
        """
        Async event handler for when client becomes active.
        """
        if not self.clipboard.is_listening():
            await self.clipboard.start()
        self._listening = True
        await asyncio.sleep(0)

    async def _on_client_inactive(self, data: dict):
        """
        Async event handler for when a client becomes inactive.
        """
        # Behavior change: We do not stop listening on inactive clients
        pass
        # if self.clipboard.is_listening():
        #     await self.clipboard.stop()
        # self._listening = False

    async def _on_client_connected(self, data: Optional[ClientConnectedEvent]):
        """
        Async event handler for when a client connects.
        """
        if data is None:
            return

        client_screen = data.client_screen
        self._active_screens[client_screen] = True
        self._listening = True

        if not self.clipboard.is_listening():
            await self.clipboard.start()
            return

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
            if self.clipboard.is_listening():
                await self.clipboard.stop()
                return

        await asyncio.sleep(0)

    async def _on_clipboard_change(self, content: str, content_type: ClipboardType):
        if self._listening:
            event = ClipboardEvent(content=content, content_type=content_type.value)
            # Send clipboard event to all connected clients -> Sync server clipboard with clients (if server)
            await self.stream_handler.send(event)

        await asyncio.sleep(0)

    def get_clipboard_context(self) -> Clipboard:
        """
        Get the clipboard context.
        Returns:
            Clipboard monitoring instance
        """
        return self.clipboard


class ClipboardController:
    """
    Base clipboard controller that handles incoming clipboard events from clients.
    Updates the local clipboard based on received events.
    """

    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        clipboard: Optional[Clipboard] = None,
    ):
        """
        Initialize the clipboard controller.
        Args:
            event_bus: Event bus for event handling
            stream_handler: Stream handler to receive clipboard events
            clipboard: Clipboard monitoring instance (default to Os-specific Clipboard)
        """
        self.event_bus = event_bus
        self.stream_handler = stream_handler

        if clipboard is None:
            raise ValueError(
                "Clipboard instance must be provided to ClipboardController"
            )

        self.clipboard = clipboard

        # self.event_bus.subscribe(event_type=EventType.CLIPBOARD_EVENT, callback=self._on_clipboard_event)
        self.stream_handler.register_receive_callback(
            self._on_clipboard_event, "clipboard"
        )

    async def start(self) -> bool:
        """
        Start the clipboard controller.
        """
        return True

    async def stop(self):
        """
        Stop the clipboard controller.
        """
        return True

    async def _on_clipboard_event(self, message):
        """
        Async event handler for incoming clipboard events from clients.
        """
        event = EventMapper.get_event(message)
        if not isinstance(event, ClipboardEvent):
            await asyncio.sleep(0)
            return

        content = event.content

        if content is not None:
            await self.clipboard.set_clipboard(content)
            return

        await asyncio.sleep(0)
