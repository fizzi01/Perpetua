import sys
import os
import asyncio

import wx
from wx.core import Point, Size

import time
import threading

from multiprocessing import Pipe, Process

if sys.platform == "win32":
    from multiprocessing.connection import PipeConnection as Connection
else:
    from multiprocessing.connection import Connection
from typing import Optional

from event import (
    EventType,
    MouseEvent,
    ActiveScreenChangedEvent,
    ClientDisconnectedEvent,
    BusEvent,
)
from event.bus import EventBus

from network.stream.handler import StreamHandler

from utils.logging import get_logger, Logger
from utils.screen import Screen

wxEVT_SCREEN_UNLOCKED = wx.NewEventType()
EVT_SCREEN_UNLOCKED = wx.PyEventBinder(wxEVT_SCREEN_UNLOCKED, 1)


class ScreenUnlockedEvent(wx.PyEvent):
    """Event to signal that the screen has been unlocked."""

    def __init__(self):
        super().__init__()
        self.SetEventType(wxEVT_SCREEN_UNLOCKED)


class CursorHandlerWindow(wx.Frame):
    """
    Base class for cursor handling window.
    Derived classes must implement platform-specific methods.
    """

    WINDOW_SIZE: Size = Size(400, 400)
    BORDER_OFFSET: int = 1

    def __init__(
        self,
        command_conn: Connection,
        result_conn: Connection,
        mouse_conn: Connection,
        debug: bool = False,
        **frame_kwargs,
    ):
        """
        Initialize the cursor handler window.
        Args:
            command_conn (Connection): Pipe connection for receiving commands.
            result_conn (Connection): Pipe connection for sending results.
            mouse_conn (Connection): Connection for sending mouse movement data.
            debug (bool): Enable debug mode.
            **frame_kwargs: Additional arguments for wx.Frame.
        """
        super().__init__(None, title="", **frame_kwargs)

        self._debug = debug
        self.mouse_captured_flag = threading.Event()
        self.mouse_captured_event = threading.Event()

        self.center_pos: Optional[Point] = None

        self.command_conn: Connection = command_conn
        self.result_conn: Connection = result_conn
        self.mouse_conn: Connection = mouse_conn

        self.previous_app = None
        self.previous_app_pid = None

        # Start command processing thread
        self._running = True
        self.command_thread = threading.Thread(target=self._process_commands)
        self.command_thread.start()

        # Panel principale
        self.panel: Optional[wx.Panel] = (
            None  # Placeholder for derived classes to customize
        )

        if not self._debug:
            self.SetTransparent(0)

        self.last_mouse_send_time = 0
        self.mouse_send_interval = 0.005
        self.accumulated_delta_x = 0
        self.accumulated_delta_y = 0

        # Screen lock monitoring
        self._screen_monitor_thread: Optional[threading.Thread] = None
        self._screen_monitor_running = False
        self._last_screen_locked_state: Optional[bool] = None

        # Events
        self.Bind(wx.EVT_MOTION, self.on_mouse_move)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key_press)
        self.Bind(wx.EVT_CLOSE, self._quit_app)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.RestoreFocus)
        self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, self.on_mouse_capture_lost)
        self.Bind(wx.EVT_KILL_FOCUS, self.on_kill_focus)
        self.Bind(wx.EVT_ACTIVATE, self.on_activate)
        self.Bind(EVT_SCREEN_UNLOCKED, self.on_screen_unlock)

        self._logger = get_logger(
            self.__class__.__name__, level=Logger.DEBUG, is_root=True
        )

    def _create(self):
        """
        Must be called by derived classes in constructor.
        """
        self.Centre()
        self.Show()
        self.HideOverlay()

    def _process_commands(self):
        """
        Commands processing loop.
        """
        try:
            while self._running:
                try:
                    if self.command_conn.poll(timeout=0.1):
                        command = self.command_conn.recv()
                        cmd_type = command.get("type")

                        if cmd_type == "enable_capture":
                            wx.CallAfter(self.enable_mouse_capture)
                        elif cmd_type == "disable_capture":
                            x, y = command.get("x", -1), command.get("y", -1)
                            wx.CallAfter(self.disable_mouse_capture, x, y)
                        elif cmd_type == "get_stats":
                            self.result_conn.send(
                                {
                                    "type": "stats",
                                    "is_captured": self.mouse_captured_flag.is_set(),
                                }
                            )
                        elif cmd_type == "quit":
                            self._running = False
                            wx.CallAfter(self._quit_app)
                except Exception:
                    # time.sleep(0)
                    continue
        except Exception as e:
            print(f"Error processing commands: {e}")

    def _quit_app(self):
        """Quit the wx application properly"""

        # Unbind all events to prevent further processing
        self._logger.debug("Quitting")

        self.Unbind(wx.EVT_MOTION)
        self.Unbind(wx.EVT_CHAR_HOOK)
        self.Unbind(wx.EVT_CLOSE)
        self.Unbind(wx.EVT_LEAVE_WINDOW)
        self.Unbind(wx.EVT_TIMER)
        self.Unbind(wx.EVT_MOUSE_CAPTURE_LOST)
        self.Unbind(wx.EVT_KILL_FOCUS)
        self.Unbind(wx.EVT_ACTIVATE)
        self.Unbind(EVT_SCREEN_UNLOCKED)

        self._logger.debug("Stopping screen monitor...")
        try:
            self._stop_screen_monitor()
        except Exception as e:
            self._logger.debug(f"Error stopping screen monitor: {e}")
            pass

        self._logger.debug("Disabling mouse capture...")
        try:
            self.disable_mouse_capture()
        except Exception as e:
            self._logger.debug(f"Error disabling mouse capture: {e}")
            pass

        self._logger.debug("Destroying window...")
        try:
            if not self.IsBeingDeleted():
                self.Destroy()
        except Exception as e:
            self._logger.debug(f"Error destroying window ({e})")
            pass

        self._logger.debug("Exiting main loop...")
        try:
            app = wx.GetApp()
            if app:
                app.DeletePendingEvents()
                app.ExitMainLoop()
        except Exception as e:
            self._logger.debug(f"Error exiting main loop ({e})")
            pass

    def on_mouse_capture_lost(self, event):
        """
        Handle the EVT_MOUSE_CAPTURE_LOST event.
        This is called by wxWidgets when the mouse capture is lost.
        """
        # if self.mouse_captured_flag.is_set():
        # self._logger.warning(
        #     "EVT_MOUSE_CAPTURE_LOST received - capture was lost by system"
        # )
        event.Skip()

    def on_kill_focus(self, event):
        """
        Handle the EVT_KILL_FOCUS event.
        This is called when the window loses focus.
        """
        if self.mouse_captured_flag.is_set():
            # self._logger.warning(
            #     "EVT_KILL_FOCUS received - window lost focus while capture was active"
            # )
            # Schedule a re-focus and re-capture attempt
            wx.CallAfter(self._attempt_recapture)
        event.Skip()

    def on_activate(self, event):
        """
        Handle the EVT_ACTIVATE event.
        This is called when window activation state changes.
        """
        is_active = event.GetActive()
        if self.mouse_captured_flag.is_set() and not is_active:
            # self._logger.warning(
            #     f"EVT_ACTIVATE received - window deactivated (active={is_active}) while capture was active"
            # )
            # Schedule a re-focus and re-capture attempt
            wx.CallAfter(self._attempt_recapture)
        event.Skip()

    def on_screen_unlock(self, event):
        """
        Handle screen unlock event.
        This is called when the screen is unlocked after being locked.
        """
        if self.mouse_captured_flag.is_set():
            self._logger.info("Screen unlocked - attempting to recapture mouse")
            wx.CallAfter(self._attempt_recapture)
        event.Skip()

    def _attempt_recapture(self):
        """
        Attempt to recapture the mouse and restore focus.
        This is called when focus is lost while capture should be active.
        """
        if not self.mouse_captured_flag.is_set():
            return

        # self._logger.info("Attempting to recapture mouse and restore focus...")
        try:
            self.Raise()
            self.SetFocus()
            self.ForceOverlay()

            # self._logger.info("Recapture attempt completed")
        except Exception as e:
            self._logger.error(f"Error during recapture attempt ({e})")

    def RestoreFocus(self, event):
        """
        Restore current window focus when mouse leaves the overlay.
        Derived classes can implement platform-specific focus restoration here
        (default: do nothing).
        """
        if event:
            event.Skip()

    def ForceOverlay(self):
        """
        Force the overlay to be visible and interactive.
        """
        try:
            self.SetSize(self.WINDOW_SIZE)
            self.Show(True)
        except Exception as e:
            self._logger.debug(f"Error forcing overlay: {e}")

    def HideOverlay(self):
        """
        Hide the overlay and restore previous application (if implemented).
        """
        try:
            self.RestorePreviousApp()
            # self.panel.Hide()
            self.Hide()
            # Resize to 0x0 to avoid interaction
            self.SetSize(Size(0, 0))
        except Exception as e:
            self._logger.debug(f"Error hiding overlay: {e}")

    def RestorePreviousApp(self):
        """
        Restore the previously active application.
        """
        raise NotImplementedError("Derived classes must implement RestorePreviousApp")

    def on_key_press(self, event):
        """
        Handle key press events for debug controls.
        """
        key_code = event.GetKeyCode()

        if self._debug:
            if key_code == wx.WXK_SPACE:
                if self.mouse_captured_flag.is_set():
                    self.disable_mouse_capture()
                else:
                    self.enable_mouse_capture()
            elif key_code == wx.WXK_ESCAPE:
                self.disable_mouse_capture()
            elif key_code == ord("Q") or key_code == ord("q"):
                self.Close()
            else:
                event.Skip()
        else:
            event.Skip()

    def handle_cursor_visibility(self, visible: bool):
        """
        Handle cursor visibility.
        If visible is False, hide the cursor. If True, show the cursor.
        Implement platform-specific cursor hiding/showing here.
        """
        raise NotImplementedError(
            "Derived classes must implement handle_cursor_visibility"
        )

    def MoveWindow(self, x: int = -1, y: int = -1) -> None:
        if x == -1 or y == -1:
            return

        # Denormalize coordinates
        screen_width, screen_height = wx.GetDisplaySize()
        x = int(x * screen_width)
        y = int(y * screen_height)

        try:
            self.Move(x, y)
        except Exception as e:
            self._logger.error(f"Error moving window ({e})")

    def _get_centered_coords(self) -> Point:
        """
        Get the coordinates to center the window on the cursor position.
        """
        cursor_pos = wx.GetMousePosition()

        display_index = wx.Display.GetFromPoint(cursor_pos)
        if display_index == wx.NOT_FOUND:
            display_index = 0
        display = wx.Display(display_index)
        screen_rect = display.GetClientArea()

        # Offset minimo dai bordi (in pixel)
        offset = self.BORDER_OFFSET

        # Calcola la posizione per centrare la finestra sul cursore
        x: int = cursor_pos.x - self.WINDOW_SIZE[0] // 2
        y: int = cursor_pos.y - self.WINDOW_SIZE[1] // 2

        # Applica i limiti considerando l'offset dai bordi
        x: int = max(
            screen_rect.x + offset - self.WINDOW_SIZE[0] // 2,
            min(
                x, screen_rect.x + screen_rect.width - offset - self.WINDOW_SIZE[0] // 2
            ),
        )
        y: int = max(
            screen_rect.y + offset - self.WINDOW_SIZE[1] // 2,
            min(
                y,
                screen_rect.y + screen_rect.height - offset - self.WINDOW_SIZE[1] // 2,
            ),
        )
        return Point(x, y)

    def _force_recapture(self):
        """
        Attempt to recapture the mouse and restore focus.
        This is called every time capture is enabled, to ensure the overlay is focused.
        (On macOS in particular, focus may not be properly set on first attempt.)

        Os-specific implementations may override this method.
        """
        pass

    def enable_mouse_capture(self):
        """
        Enable mouse capture.
        """
        if not self.mouse_captured_flag.is_set():
            # Forza il focus prima di catturare
            self.Raise()
            self.SetFocus()
            self.ForceOverlay()
            wx.Sleep(0)

            # Nascondi il cursore
            self.handle_cursor_visibility(False)

            # Calcola il centro della finestra
            size = self.GetSize()
            pos = self.GetPosition()
            self.center_pos = Point(pos.x + size.width // 2, pos.y + size.height // 2)

            # Cattura il mouse
            while not self.HasCapture():
                self.CaptureMouse()
            self.mouse_captured_flag.set()
            wx.Sleep(0)

            self._force_recapture()

            self.reset_mouse_position()
            self.result_conn.send({"type": "capture_enabled", "success": True})

            # Start screen lock monitoring
            wx.CallAfter(self._start_screen_monitor)

    def disable_mouse_capture(self, x: int = -1, y: int = -1):
        """
        Disable mouse capture.
        """
        if self.mouse_captured_flag.is_set():
            self.Unbind(wx.EVT_MOTION)
            self.MoveWindow(x, y)
            wx.Sleep(0)
            time.sleep(0)
            self.mouse_captured_flag.clear()
            wx.Sleep(0)
            time.sleep(0)

            # Rilascia il mouse
            while self.HasCapture():
                self.ReleaseMouse()
            wx.Sleep(0)
            # wx.SafeYield()

            self.result_conn.send({"type": "capture_disabled", "success": True})

            # Ripristina il cursore
            self.HideOverlay()
            self.handle_cursor_visibility(True)
            wx.Sleep(0)

            self.Bind(wx.EVT_MOTION, self.on_mouse_move)  # Rebind MOTION event

            # Stop screen lock monitoring
            wx.CallAfter(self._stop_screen_monitor)

    def reset_mouse_position(self):
        """
        Reset mouse position to center.
        """
        if self.mouse_captured_flag.is_set() and self.center_pos is not None:
            # Sposta il cursore al centro della finestra
            client_center = self.ScreenToClient(self.center_pos)
            self.WarpPointer(client_center.x, client_center.y)

    def on_mouse_move(self, event):
        """
        Handle mouse movement events.
        """
        if not self.mouse_captured_flag.is_set() or self.center_pos is None:
            time.sleep(0)
            event.Skip()
            return

        # Ottieni posizione corrente
        mouse_pos = wx.GetMousePosition()

        # Calcola delta rispetto al centro
        delta_x = mouse_pos.x - self.center_pos.x
        delta_y = mouse_pos.y - self.center_pos.y

        # Processa solo se c'è movimento
        if delta_x != 0 or delta_y != 0:
            self.accumulated_delta_x += delta_x
            self.accumulated_delta_y += delta_y

            current_time = time.time()
            if current_time - self.last_mouse_send_time >= self.mouse_send_interval:
                try:
                    self.mouse_conn.send(
                        (self.accumulated_delta_x, self.accumulated_delta_y)
                    )
                    self.accumulated_delta_x = 0
                    self.accumulated_delta_y = 0
                    self.last_mouse_send_time = current_time
                except Exception:
                    pass

            # Resetta posizione
            time.sleep(0)
            self.reset_mouse_position()
            time.sleep(0)

        time.sleep(0)
        event.Skip()

    def on_close(self, event):
        """
        Handle window close event.
        """
        self._running = False
        self.disable_mouse_capture()
        self.Destroy()

    def _screen_monitor_loop(self):
        """
        Monitor loop that checks for screen lock/unlock transitions.
        Runs in a separate thread and posts events to the main thread.
        """
        self._logger.debug("Screen monitor thread started")
        self._last_screen_locked_state = Screen.is_screen_locked()

        while self._screen_monitor_running:
            try:
                current_locked_state = Screen.is_screen_locked()

                # Detect transition from locked to unlocked
                if self._last_screen_locked_state and not current_locked_state:
                    self._logger.info("Screen unlock detected")
                    # Post event to main thread
                    wx.PostEvent(self, ScreenUnlockedEvent())

                self._last_screen_locked_state = current_locked_state

                time.sleep(0.1)

            except Exception as e:
                self._logger.error(f"Error in screen monitor loop ({e})")
                time.sleep(1)

        self._logger.debug("Screen monitor thread stopped")

    def _start_screen_monitor(self):
        """Start the screen lock monitoring thread."""
        if (
            self._screen_monitor_thread is not None
            and self._screen_monitor_thread.is_alive()
        ):
            return  # Already running

        self._screen_monitor_running = True
        self._screen_monitor_thread = threading.Thread(
            target=self._screen_monitor_loop, daemon=True
        )
        self._screen_monitor_thread.start()
        # self._logger.debug("Screen monitor started")

    def _stop_screen_monitor(self):
        """Stop the screen lock monitoring thread."""
        if self._screen_monitor_thread is None:
            return

        self._screen_monitor_running = False
        if self._screen_monitor_thread.is_alive():
            self._screen_monitor_thread.join(timeout=2.0)
        self._screen_monitor_thread = None
        # self._logger.debug("Screen monitor stopped")


class _CursorHandlerProcess:
    """
    Internal class to run the cursor handler window in a separate process.
    """

    @staticmethod
    def _cleanup(
        logger,
        command_conn: Connection,
        result_conn: Connection,
        mouse_conn: Connection,
    ):
        """Clean up pipes in child process"""
        try:
            # Drain connections
            while command_conn.poll():
                try:
                    command_conn.recv()
                except Exception:
                    break

            # Close resources
            command_conn.close()
            result_conn.close()
            mouse_conn.close()
        except Exception as e:
            logger.error(f"Error during cleanup ({e})")
            pass  # Ignore errors

    @staticmethod
    def run(
        command_conn: Connection,
        result_conn: Connection,
        mouse_conn: Connection,
        debug: bool = False,
        window_class=CursorHandlerWindow,
    ):
        """Run the cursor handler window process"""
        _logger = get_logger(
            "_CursorHandlerProcess",
            level=Logger.DEBUG,
            is_root=True,
        )

        _logger.debug("Starting...", pid=os.getpid())
        app = None
        window = None
        try:
            app = wx.App()
            window = window_class(
                command_conn=command_conn,
                result_conn=result_conn,
                mouse_conn=mouse_conn,
                debug=debug,
            )

            # Notify that the window is ready
            result_conn.send({"type": "window_ready"})
            _logger.debug("Entering main loop")
            app.MainLoop()
            result_conn.send({"type": "process_ended"})
            _logger.debug("Main loop left")
        except Exception as e:
            _logger.error(f"{e}")
        finally:
            # Clean up wx resources first
            try:
                if window and not window.IsBeingDeleted():
                    window.Destroy()
            except Exception as e:
                _logger.error(f"Error destroying window ({e})")
                pass

            try:
                if app:
                    app.DeletePendingEvents()
                    app.ExitMainLoop()
            except Exception as e:
                _logger.error(f"Error exiting app main loop ({e})")
                pass

            # Then clean up IPC resources
            _CursorHandlerProcess._cleanup(
                _logger, command_conn, result_conn, mouse_conn
            )

            _logger.debug("Process exiting")


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
        window_class=CursorHandlerWindow,
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
        self._mouse_data_task = None  # Async task instead of thread

        self._active_client: Optional[str] = None
        self._last_event: Optional[BusEvent] = None

        self.window_class = window_class

        self._logger = get_logger(self.__class__.__name__)

        # Register to active_screen with async callbacks
        self.event_bus.subscribe(
            event_type=EventType.SCREEN_CHANGE_GUARD,
            callback=self._on_screen_change_guard,
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected,
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
                event_type=EventType.ACTIVE_SCREEN_CHANGED,
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
                event_type=EventType.ACTIVE_SCREEN_CHANGED,
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
            target=_CursorHandlerProcess.run,
            args=(
                self.command_conn_rec,
                self.result_conn_send,
                self.mouse_conn_send,
                self._debug,
                self.window_class,
            ),
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
            # Aspetta che la window sia pronta
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
        """Ferma il processo della window e cleanup async task"""
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
                self._logger.warning(f"Error stopping process -> {e}")

        # Clean up resources
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._drain_pipe, self.command_conn_send)
            await loop.run_in_executor(None, self._drain_pipe, self.result_conn_rec)
        except Exception as e:
            self._logger.warning(f"Error draining pipes -> {e}")

        # Close handles
        try:
            self.command_conn_send.close()
            self.command_conn_rec.close()
            self.result_conn_send.close()
            self.result_conn_rec.close()
            self.mouse_conn_send.close()
            self.mouse_conn_rec.close()
        except Exception as e:
            self._logger.warning(f"Error closing connections -> {e}")

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
        """Controlla se il processo della window è in esecuzione"""
        return self._is_running and self.process is not None and self.process.is_alive()

    async def _mouse_data_listener(self):
        """
        Async coroutine per ascoltare i dati del mouse dal processo di cattura.
        Legge dal pipe in modo non-bloccante usando executor.
        """
        loop = asyncio.get_running_loop()

        while self._is_running and self.stream is not None:
            try:
                # Poll non-bloccante
                has_data = await loop.run_in_executor(
                    None, self.mouse_conn_rec.poll, self.DATA_POLL_TIMEOUT
                )

                if has_data:
                    # Leggi dal pipe in executor
                    delta_x, delta_y = await loop.run_in_executor(
                        None, self.mouse_conn_rec.recv
                    )  # type: ignore # ty:ignore[unused-ignore-comment]

                    mouse_event = MouseEvent(action=MouseEvent.MOVE_ACTION)
                    mouse_event.dx = delta_x
                    mouse_event.dy = delta_y

                    # Invio async via stream
                    await self.stream.send(mouse_event)
                else:
                    # Piccolo sleep per evitare busy waiting
                    await asyncio.sleep(0.0001)

            except EOFError:
                await asyncio.sleep(0)
                break
            except Exception as e:
                self._logger.exception(f"Error in mouse data listener ({e})")
                await asyncio.sleep(0.01)

    async def send_command(self, command):
        """Invia un comando alla window in modo asincrono"""
        if not self._is_running:
            raise RuntimeError("Window process not running")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.command_conn_send.send, command)  # type: ignore  # ty:ignore[unused-ignore-comment]
        await asyncio.sleep(0)  # Yield control to event loop

    async def get_result(self, timeout: float = RESULT_POLL_TIMEOUT):
        """Riceve un risultato dalla window in modo asincrono"""
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
        """Riceve tutti i risultati disponibili"""
        results = []
        while True:
            result = await self.get_result(timeout=timeout)
            if result is None:
                break
            results.append(result)
        return results

    async def enable_capture(self):
        """Abilita la cattura del mouse in modo asincrono"""
        await self.send_command({"type": "enable_capture"})
        return await self.get_result()

    async def disable_capture(self, **kwargs):
        """Disabilita la cattura del mouse in modo asincrono"""
        await self.send_command({"type": "disable_capture", **kwargs})
        return await self.get_result()

    async def close_handler(self, **kwargs):
        await self.send_command({"type": "quit"})
        return await asyncio.sleep(0)

    async def set_message(self, message):
        """Imposta un messaggio nella window in modo asincrono"""
        await self.send_command({"type": "set_message", "message": message})
        return await self.get_result()
