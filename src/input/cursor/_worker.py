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

import sys
import asyncio
import threading
import time

from multiprocessing import Pipe, Process

if sys.platform == "win32":
    from multiprocessing.connection import PipeConnection as Connection
else:
    from multiprocessing.connection import Connection
from typing import Optional

from event import (
    BusEventType,
    MouseEvent,
    ActiveScreenChangedEvent,
    ClientDisconnectedEvent,
    BusEvent,
)
from event.bus import EventBus

from network.stream.handler import StreamHandler

from utils.logging import get_logger


class CursorHandlerWorker(object):
    """
    Base class for cursor handler worker.
    Manages the cursor handler window process and communication.
    There is no platform-specific code here, all platform specifics are in the window class.
    """

    RESULT_POLL_TIMEOUT = 0.1  # seconds
    DATA_POLL_TIMEOUT = 0.0001  # seconds

    def __init__(
        self,
        event_bus: EventBus,
        stream: Optional[StreamHandler] = None,
        debug: bool = False,
        window_class=None,
    ):
        """
        Initialize the cursor handler worker.

        Args:
            event_bus (EventBus): The event bus for communication.
            stream (Optional[StreamHandler]): The stream handler for mouse events.
            debug (bool): Enable debug mode.
            window_class: The window class to use for cursor handling.
        """
        self.event_bus = event_bus
        self.stream = stream

        self._debug = debug

        # Bidirectional pipes for command/result communication
        self.command_conn_rec, self.command_conn_send = Pipe(duplex=False)
        self.result_conn_rec, self.result_conn_send = Pipe(duplex=False)

        # Unidirectional pipe for mouse movement
        self.mouse_conn_rec, self.mouse_conn_send = Pipe(duplex=False)
        self.process = None
        self._is_running = False
        self._mouse_data_task = None  # Async forwarder task

        # Dedicated thread that owns mouse_conn_rec: blocking reads avoid the
        # per-delta run_in_executor scheduling overhead. Deltas land on
        # _mouse_data_queue via call_soon_threadsafe.
        self._mouse_data_thread: Optional[threading.Thread] = None
        self._mouse_thread_stop = threading.Event()
        self._mouse_data_queue: Optional[asyncio.Queue] = None

        self._active_client: Optional[str] = None
        self._last_event: Optional[BusEvent] = None

        self.window_class = window_class

        self._logger = get_logger(self.__class__.__name__)

        # Register to active_screen with async callbacks
        self.event_bus.subscribe(
            event_type=BusEventType.SCREEN_CHANGE_GUARD,
            callback=self._on_screen_change_guard,
        )
        self.event_bus.subscribe(
            event_type=BusEventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected,
        )

    def _get_process_target(self):
        """Return the callable used as the Process target.
        Platform-specific subclasses must override this.
        """
        raise NotImplementedError("Subclasses must override _get_process_target")

    def _get_process_args(self):
        """Return the args tuple passed to the Process target.
        Platform-specific subclasses may override this.
        """
        return (
            self.command_conn_rec,
            self.result_conn_send,
            self.mouse_conn_send,
            self._debug,
            self.window_class,
            self._logger.level,
        )

    async def _on_screen_change_guard(
        self, data: Optional[ActiveScreenChangedEvent]
    ) -> None:
        """Async callback for active screen changed"""

        if data is None:
            return

        active_screen = data.active_screen
        self._last_event = data

        if active_screen:
            # Start capture cursor
            # dispatch event before enabling capture to set correct cursor position
            await self.event_bus.dispatch(
                # when ServerMouseController receives this event will set the correct cursor position
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=self._last_event,
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            try:
                await self.enable_capture()
            except Exception as e:
                self._logger.error(f"Error enabling cursor capture: {e}")
            self._active_client = active_screen
        else:
            try:
                await self.disable_capture(x=data.x, y=data.y)
            except Exception as e:
                self._logger.error(f"Error disabling cursor capture: {e}")
            # dispatch event after disabling capture
            await self.event_bus.dispatch(
                # when ServerMouseController receives this event will set the correct cursor position
                event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
                data=self._last_event,
            )
            self._active_client = None

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        """Async callback for client inactive"""
        if data is None:
            return

        if self._active_client and data.client_screen == self._active_client:
            self._active_client = None
            await self.disable_capture()
            return

        await asyncio.sleep(0)

    async def start(self, wait_ready=True, timeout=1) -> bool:
        """Starts the process responsible for handling cursor and mouse events, along
        with the associated worker processes. Ensures that the initialization phase
        completes successfully when `wait_ready` is set to True.

        Args:
            wait_ready (bool): Specifies whether to wait for the window to signal that
                it is ready. Defaults to True.
            timeout (float): The maximum time, in seconds, to wait for the window to
                signal readiness if `wait_ready` is set to True. Defaults to 1 second.

        Returns:
            bool: True when the process starts successfully.

        Raises:
            TimeoutError: If `wait_ready` is True and the window is not ready within the
                specified timeout.
        """
        if self._is_running:
            await asyncio.sleep(0)
            return True

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise RuntimeError("No running event loop found")

        self.process = Process(
            target=self._get_process_target(),
            args=self._get_process_args(),
            daemon=True,
        )
        self.process.start()
        self._is_running = True

        if self.stream is not None:
            # Start async task for mouse data listener
            try:
                self._mouse_data_task = asyncio.create_task(self._mouse_data_listener())
            except RuntimeError:
                self._logger.error("Error creating task for mouse data listener")
                self._mouse_data_task = None
                return False

        if wait_ready:
            # Wait for the window to be ready
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    has_data = await loop.run_in_executor(
                        None,
                        self.result_conn_rec.poll,
                        0.1,
                    )
                    if has_data:
                        result = await loop.run_in_executor(
                            None,
                            self.result_conn_rec.recv,
                        )
                        if result.get("type") == "window_ready":
                            self._logger.debug("Started")
                            await asyncio.sleep(0)
                            return True

                    await asyncio.sleep(0)
                except Exception:
                    await asyncio.sleep(0)
                    continue
            raise TimeoutError("Window not ready in time")

        self._logger.debug("Started (without checks)")
        return True

    async def stop(self, timeout=2):
        """Stops the window process and cleans up async task"""
        if not self._is_running:
            return

        try:
            await self.close_handler()
            await asyncio.sleep(0.2)
        except (RuntimeError, BrokenPipeError, EOFError):
            # Process not running or already dead
            pass

        self._is_running = False  # Set this early to stop any listeners

        # Cancel async task if running
        if self._mouse_data_task:
            self._mouse_data_task.cancel()
            try:
                await self._mouse_data_task
            except asyncio.CancelledError:
                pass
            self._mouse_data_task = None
            pass

        # Wait for child process to terminate completely
        if self.process:
            try:
                loop = asyncio.get_running_loop()

                # Try graceful shutdown first (short timeout)
                if self.process.is_alive():
                    self._logger.debug(
                        "Waiting for graceful shutdown...", pid=self.process.pid
                    )
                    await loop.run_in_executor(None, self.process.join, 1.0)

                # If still alive, terminate
                if self.process.is_alive():
                    self._logger.warning(
                        "Process still alive after quit, terminating...",
                        pid=self.process.pid,
                    )
                    self.process.terminate()
                    await loop.run_in_executor(None, self.process.join, 0.5)

                # If STILL alive, kill
                if self.process.is_alive():
                    self._logger.warning(
                        "Process still alive after terminate, killing...",
                        pid=self.process.pid,
                    )
                    self.process.kill()
                    await loop.run_in_executor(None, self.process.join, None)

                # Final check
                if self.process.is_alive():
                    self._logger.critical(
                        "Process STILL alive after kill!", pid=self.process.pid
                    )
                else:
                    self._logger.debug(
                        f"Process terminated with exitcode: {self.process.exitcode}",
                        pid=self.process.pid,
                    )

            except Exception as e:
                self._logger.warning(f"Error stopping process ({e})")

        # Clean up resources
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._drain_pipe, self.command_conn_send)
            await loop.run_in_executor(None, self._drain_pipe, self.result_conn_rec)
        except Exception as e:
            self._logger.warning(f"Error draining pipes ({e})")

        # Close handles
        try:
            self.command_conn_send.close()
            self.command_conn_rec.close()
            self.result_conn_send.close()
            self.result_conn_rec.close()
            self.mouse_conn_send.close()
            self.mouse_conn_rec.close()
        except Exception as e:
            self._logger.warning(f"Error closing connections ({e})")

        self._logger.debug("Stopped")

    @staticmethod
    def _drain_pipe(conn: Connection):
        """Drain all items from a pipe"""
        try:
            while conn.poll():
                conn.recv()
        except Exception:
            pass

    def is_alive(self) -> bool:
        """Checks if the window process is running"""
        return self._is_running and self.process is not None and self.process.is_alive()

    # Bound on how many in-flight deltas we keep buffered between the OS-thread
    # reader and the asyncio forwarder. Old deltas are dropped if the consumer
    # (network stream) can't keep up — cursor jitter is preferable to growing
    # backpressure on every mouse move.
    _MOUSE_QUEUE_MAXSIZE = 4096

    def _mouse_data_reader_thread(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: "asyncio.Queue",
    ) -> None:
        """
        Runs on a dedicated thread. Owns mouse_conn_rec and blocks on recv
        (with a short poll timeout so we can react to shutdown). Each delta
        is handed to the asyncio queue via call_soon_threadsafe.
        """
        conn = self.mouse_conn_rec
        stop = self._mouse_thread_stop
        while not stop.is_set():
            try:
                if not conn.poll(0.1):
                    continue
                delta = conn.recv()
            except (EOFError, OSError):
                break
            except Exception:
                # Pipe closed mid-shutdown or transient OS-level error: bail out.
                break
            try:
                loop.call_soon_threadsafe(self._enqueue_mouse_delta, queue, delta)
            except RuntimeError:
                # Loop closing.
                break

    @staticmethod
    def _enqueue_mouse_delta(queue: "asyncio.Queue", delta) -> None:
        """Drop-oldest enqueue: cursor deltas are lossy by design."""
        try:
            queue.put_nowait(delta)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(delta)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def _mouse_data_listener(self):
        """
        Forwards mouse deltas from the reader thread onto the stream. Each
        iteration awaits a single asyncio.Queue.get; the actual blocking pipe
        read happens on the dedicated thread.
        """
        loop = asyncio.get_running_loop()
        self._mouse_data_queue = asyncio.Queue(maxsize=self._MOUSE_QUEUE_MAXSIZE)
        self._mouse_thread_stop.clear()
        self._mouse_data_thread = threading.Thread(
            target=self._mouse_data_reader_thread,
            args=(loop, self._mouse_data_queue),
            name="CursorMouseDeltaReader",
            daemon=True,
        )
        self._mouse_data_thread.start()

        try:
            queue = self._mouse_data_queue
            while self._is_running and self.stream is not None:
                try:
                    delta_x, delta_y = await queue.get()
                except asyncio.CancelledError:
                    raise
                # Fresh event per iteration: stream.send may await between
                # iterations, so reusing one object would race delta values.
                mouse_event = MouseEvent(
                    action=MouseEvent.MOVE_ACTION, dx=delta_x, dy=delta_y
                )
                try:
                    await self.stream.send(mouse_event)
                except Exception as e:
                    self._logger.exception(f"Error sending mouse delta ({e})")
                    await asyncio.sleep(0.01)
        finally:
            # Signal the reader thread to wind down on cancellation/loop exit.
            self._mouse_thread_stop.set()
            if self._mouse_data_thread is not None:
                # join from an executor so we don't block the loop here.
                t = self._mouse_data_thread
                self._mouse_data_thread = None
                try:
                    await loop.run_in_executor(None, t.join, 1.0)
                except Exception:
                    pass

    async def send_command(self, command):
        """Sends a command to the window asynchronously"""
        if not self._is_running:
            raise RuntimeError("Window process not running")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.command_conn_send.send, command)  # type: ignore  # ty:ignore[unused-ignore-comment]
        await asyncio.sleep(0)  # Yield control to event loop

    async def get_result(self, timeout: float = RESULT_POLL_TIMEOUT):
        """Receives a result from the window asynchronously"""
        loop = asyncio.get_running_loop()
        try:
            has_data = await loop.run_in_executor(
                None,
                self.result_conn_rec.poll,
                timeout,
            )
            if has_data:
                return await loop.run_in_executor(  # type: ignore # ty:ignore[unused-ignore-comment]
                    None,
                    self.result_conn_rec.recv,
                )
            return None
        except Exception:
            return None

    async def get_all_results(self, timeout=RESULT_POLL_TIMEOUT):
        """Receives all available results"""
        results = []
        while True:
            result = await self.get_result(timeout=timeout)
            if result is None:
                break
            results.append(result)
        return results

    async def enable_capture(self):
        """Enables mouse capture asynchronously"""
        await self.send_command({"type": "enable_capture"})
        return await self.get_result()

    async def disable_capture(self, **kwargs):
        """Disables mouse capture asynchronously"""
        await self.send_command({"type": "disable_capture", **kwargs})
        return await self.get_result()

    async def close_handler(self, **kwargs):
        await self.send_command({"type": "quit"})
        return await asyncio.sleep(0)

    async def set_message(self, message):
        """Sets a message in the window asynchronously"""
        await self.send_command({"type": "set_message", "message": message})
        return await self.get_result()
