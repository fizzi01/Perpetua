import sys
from queue import Empty
import asyncio

import wx
from wx.core import Point, Size

import time
import threading

from multiprocessing import Queue, Pipe, Process

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

from utils.logging import get_logger


# TODO : We need to check if a mouse is available
class CursorHandlerWindow(wx.Frame):
    """
    Base class for cursor handling window.
    Derived classes must implement platform-specific methods.
    """

    def __init__(
            self,
            command_queue: Queue,
            result_queue: Queue,
            mouse_conn: Connection,
            debug: bool = False,
            **frame_kwargs,
    ):
        """
        Initialize the cursor handler window.
        Args:
            command_queue (Queue): Queue for receiving commands.
            result_queue (Queue): Queue for sending results.
            mouse_conn (Connection): Connection for sending mouse movement data.
            debug (bool): Enable debug mode.
            **frame_kwargs: Additional arguments for wx.Frame.
        """
        super().__init__(None, title="", **frame_kwargs)

        self._debug = debug
        self.mouse_captured_flag = threading.Event()
        self.mouse_captured_event = threading.Event()

        self.center_pos: Optional[Point] = None

        self.command_queue: Queue = command_queue
        self.result_queue: Queue = result_queue
        self.mouse_conn: Connection = mouse_conn

        self.previous_app = None
        self.previous_app_pid = None

        # Start command processing thread
        self._running = True
        self.command_thread = threading.Thread(
            target=self._process_commands, daemon=True
        )
        self.command_thread.start()

        # Panel principale
        self.panel: Optional[wx.Panel] = (
            None  # Placeholder for derived classes to customize
        )

        if not self._debug:
            self.SetTransparent(0)

        self.last_mouse_send_time = 0
        self.mouse_send_interval = 0.005  # 1000 Hz
        self.accumulated_delta_x = 0
        self.accumulated_delta_y = 0

        # Eventi
        self.Bind(wx.EVT_MOTION, self.on_mouse_move)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key_press)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.RestoreFocus)

    def _create(self):
        """
        Must be called by derived classes in constructor.
        """
        self.Centre()
        self.Show()
        self.HideOverlay()

    def _os_delay(self, specific: bool = False):
        """
        OS-specific delay to allow UI updates.
        Derived classes can implement platform-specific delays here (default: time.sleep).

        Args:
            specific (bool): If True, use a specific os delay method.

        Default implementation just wait for mouse_captured_event to be set or timeout.
        """
        self.mouse_captured_event.wait(1)

    def _process_commands(self):
        """
        Commands processing loop.
        """
        try:
            while self._running:
                try:
                    command = self.command_queue.get(timeout=0.1)
                    cmd_type = command.get("type")

                    if cmd_type == "enable_capture":
                        wx.CallAfter(self.enable_mouse_capture)
                    elif cmd_type == "disable_capture":
                        wx.CallAfter(self.disable_mouse_capture)
                    elif cmd_type == "get_stats":
                        self.result_queue.put(
                            {
                                "type": "stats",
                                "is_captured": self.mouse_captured_flag.is_set(),
                            }
                        )
                    elif cmd_type == "quit":
                        self._running = False
                        self.Close()
                except Empty:
                    # time.sleep(0)
                    continue
        except Exception as e:
            print(f"Error processing commands: {e}")

    def RestoreFocus(self, event):
        """
        Restore current window focus when mouse leaves the overlay.
        Derived classes can implement platform-specific focus restoration here
        (default: do nothing).
        """
        event.Skip()

    def ForceOverlay(self):
        """
        Force the overlay to be visible and interactive.
        """
        try:
            self.SetSize(400, 400)
            self.Show(True)
        except Exception as e:
            print(f"Error forcing overlay: {e}")

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
            print(f"Error hiding overlay: {e}")

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

    def update_ui(self, panel_obj, data, call):
        """
        Update UI elements safely.
        """
        raise NotImplementedError("Derived classes must implement update_ui")

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
            self.center_pos: Point = Point(
                pos.x + size.width // 2, pos.y + size.height // 2
            )

            # Cattura il mouse
            if not self.HasCapture():
                self.CaptureMouse()
            self.mouse_captured_flag.set()
            wx.Sleep(0)

            self.reset_mouse_position()
            self.result_queue.put(
                {"type": "capture_enabled", "success": True}
            )

    def disable_mouse_capture(self):
        """
        Disable mouse capture.
        """
        if self.mouse_captured_flag.is_set():
            self.Unbind(wx.EVT_MOTION)
            self.mouse_captured_flag.clear()
            wx.Sleep(0)
            time.sleep(0)

            # Rilascia il mouse
            while self.HasCapture():
                self.ReleaseMouse()
            wx.Sleep(0)
            #wx.SafeYield()

            self.result_queue.put(
                {"type": "capture_disabled", "success": True}
            )

            # Ripristina il cursore
            self.HideOverlay()
            self.handle_cursor_visibility(True)
            wx.Sleep(0)

            self.Bind(wx.EVT_MOTION, self.on_mouse_move)  # Rebind MOTION event

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


class _CursorHandlerProcess:
    """
    Internal class to run the cursor handler window in a separate process.
    """

    def __init__(
            self,
            command_queue: Queue,
            result_queue: Queue,
            mouse_conn: Connection,
            debug: bool = False,
            window_class=CursorHandlerWindow,
    ):
        self.command_queue: Queue[dict] = command_queue
        self.result_queue: Queue[dict] = result_queue
        self.mouse_conn = mouse_conn
        self.window = None
        self.app = None
        self.running = False
        self.window_class = window_class
        self._debug = debug

    def run(self):
        self.app = wx.App()
        self.window = self.window_class(
            command_queue=self.command_queue,
            result_queue=self.result_queue,
            mouse_conn=self.mouse_conn,
            debug=self._debug,
        )

        # Notify that the window is ready
        self.result_queue.put({"type": "window_ready"})
        self.running = True
        self.app.MainLoop()
        self.result_queue.put({"type": "process_ended"})


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

        self.command_queue: Queue[dict] = Queue()
        self.result_queue: Queue[dict] = Queue()

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
        )  # ty:ignore[invalid-argument-type]
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected,
        )  # ty:ignore[invalid-argument-type]

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
                await self.disable_capture()
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

    def start(self, wait_ready=True, timeout=1) -> bool:
        """Avvia il processo della window"""
        if self._is_running:
            return True

        self.process = Process(
            target=_CursorHandlerProcess(
                command_queue=self.command_queue,
                result_queue=self.result_queue,
                mouse_conn=self.mouse_conn_send,
                window_class=self.window_class,
                debug=self._debug,
            ).run
        )
        self.process.start()
        self._is_running = True

        if self.stream is not None:
            # Start async task for mouse data listener
            try:
                loop = asyncio.get_running_loop()
                self._mouse_data_task = loop.create_task(self._mouse_data_listener())
            except RuntimeError:
                # No event loop running, will start manually
                pass

        if wait_ready:
            # Aspetta che la window sia pronta
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    result = self.result_queue.get(timeout=0.1)
                    if result.get("type") == "window_ready":
                        self._logger.debug("Started")
                        return True
                except Empty:
                    continue
            raise TimeoutError("Window not ready in time")

        self._logger.debug("Started (without checks)")
        return True

    async def stop(self, timeout=2):
        """Ferma il processo della window e cleanup async task"""
        if not self._is_running:
            return

        # Cancel async task if running
        if self._mouse_data_task:
            self._mouse_data_task.cancel()
            try:
                await self._mouse_data_task
            except asyncio.CancelledError:
                pass
            self._mouse_data_task = None

        await self.send_command({"type": "quit"})

        if self.process:
            # Run process join in executor to avoid blocking
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.process.join, timeout)
            if self.process.is_alive():
                self.process.terminate()
                await loop.run_in_executor(None, self.process.join, 1)

        # Close queues
        self.command_queue.close()
        self.result_queue.close()

        self.mouse_conn_send.close()
        self.mouse_conn_rec.close()

        self._is_running = False

        self._logger.debug("Stopped")

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
                    )  # type: ignore

                    mouse_event = MouseEvent(action=MouseEvent.MOVE_ACTION)
                    mouse_event.dx = delta_x
                    mouse_event.dy = delta_y

                    # Invio async via stream
                    await self.stream.send(mouse_event)
                else:
                    # Piccolo sleep per evitare busy waiting
                    await asyncio.sleep(0.0001)

            except EOFError:
                break
            except Exception as e:
                self._logger.exception(f"Error in mouse data listener ({e})")
                await asyncio.sleep(0.01)

    async def send_command(self, command):
        """Invia un comando alla window in modo asincrono"""
        if not self._is_running:
            raise RuntimeError("Window process not running")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.command_queue.put, command)  # type: ignore
        await asyncio.sleep(0)  # Yield control to event loop

    async def get_result(self, timeout: float = RESULT_POLL_TIMEOUT):
        """Riceve un risultato dalla window in modo asincrono"""
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(  # type: ignore
                None,
                self.result_queue.get,
                timeout,  # type: ignore
            )
        except Empty:
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

    async def disable_capture(self):
        """Disabilita la cattura del mouse in modo asincrono"""
        await self.send_command({"type": "disable_capture"})
        return await self.get_result()

    async def set_message(self, message):
        """Imposta un messaggio nella window in modo asincrono"""
        await self.send_command({"type": "set_message", "message": message})
        return await self.get_result()
