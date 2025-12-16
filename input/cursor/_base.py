from collections import deque
from time import time
import asyncio

import wx
import time

from multiprocessing import Pipe, Process
from multiprocessing.connection import Connection
from typing import Optional, Dict, Any

from event import EventType, MouseEvent, ActiveScreenChangedEvent, ClientDisconnectedEvent
from event.bus import EventBus

from network.stream import StreamHandler

from utils.logging import get_logger

# TODO : We need to check if a mouse is available
class CursorHandlerWindow(wx.Frame):
    """
    Base class for cursor handling window.
    Derived classes must implement platform-specific methods.
    """
    def __init__(self, command_deque, result_deque, mouse_conn: Connection, debug: bool = False, **frame_kwargs):
        """
        Initialize the cursor handler window.
        Args:
            command_deque: Shared deque for receiving commands (thread-safe).
            result_deque: Shared deque for sending results (thread-safe).
            mouse_conn (Connection): Connection for sending mouse movement data.
            debug (bool): Enable debug mode.
            **frame_kwargs: Additional arguments for wx.Frame.
        """
        self._logger = get_logger(self.__class__.__name__)
        super().__init__(None, title="", **frame_kwargs)

        self._debug = debug
        self.mouse_captured = False
        self.center_pos = None

        # Use shared deques instead of Queue for better performance
        self.command_deque = command_deque
        self.result_deque = result_deque
        self.mouse_conn: Connection = mouse_conn

        self.previous_app = None
        self.previous_app_pid = None

        self._running = True
        self.command_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_timer_tick, self.command_timer)
        self.command_timer.Start(1)

        # Panel principale
        self.panel: Optional[wx.Panel] = None # Placeholder for derived classes to customize

        if not self._debug:
            self.SetTransparent(0)

        # Eventi
        self.Bind(wx.EVT_MOTION, self.on_mouse_move)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key_press)
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def _create(self):
        """
        Must be called by derived classes in constructor.
        """
        self.Centre()
        self.Show()
        self.HideOverlay()

    def _on_timer_tick(self, event):
        """
        Timer callback for non-blocking command polling.
        Much faster than thread-based queue.get() with timeout.
        """
        try:
            # Process all pending commands in one tick (non-blocking)
            commands_processed = 0
            max_commands_per_tick = 5  # Limit to avoid UI freezing

            while commands_processed < max_commands_per_tick:
                try:
                    # Non-blocking pop from shared list (ListProxy doesn't have popleft)
                    if len(self.command_deque) == 0:
                        break
                    command = self.command_deque.pop(0)
                    commands_processed += 1

                    cmd_type = command.get('type')

                    if cmd_type == 'enable_capture':
                        self.enable_mouse_capture()
                        self.result_deque.append({'type': 'capture_enabled', 'success': True})
                        self._logger.debug('Capture enabled')
                    elif cmd_type == 'disable_capture':
                        self.disable_mouse_capture()
                        self.result_deque.append({'type': 'capture_disabled', 'success': True})
                        self._logger.debug('Capture disabled')
                    elif cmd_type == 'get_stats':
                        self.result_deque.append({
                            'type': 'stats',
                            'is_captured': self.mouse_captured,
                        })
                    elif cmd_type == 'set_message':
                        message = command.get('message', '')
                        if self.panel is not None and hasattr(self.panel, 'info_text'):
                            self.panel.info_text.SetLabel(message)
                        self.result_deque.append({'type': 'message_set', 'success': True})
                    elif cmd_type == 'quit':
                        self._running = False
                        self.command_timer.Stop()
                        self.Close()

                except IndexError:
                    # Deque is empty, break
                    break

        except Exception as e:
            self._logger.error(f"Error processing commands: {e}")

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
            #self.panel.Hide()
            self.Hide()
            # Resize to 0x0 to avoid interaction
            self.SetSize((0, 0))
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
                if self.mouse_captured:
                    self.disable_mouse_capture()
                else:
                    self.enable_mouse_capture()
            elif key_code == wx.WXK_ESCAPE:
                self.disable_mouse_capture()
            elif key_code == ord('Q') or key_code == ord('q'):
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
        raise NotImplementedError("Derived classes must implement handle_cursor_visibility")

    def update_ui(self, panel_obj, data, call):
        """
        Update UI elements safely.
        """
        raise NotImplementedError("Derived classes must implement update_ui")

    def enable_mouse_capture(self):
        """
        Enable mouse capture.
        """
        if not self.mouse_captured:
            # Forza il focus prima di catturare
            self.Raise()
            self.SetFocus()
            self.ForceOverlay()

            self.mouse_captured = True

            # Calcola il centro della finestra
            size = self.GetSize()
            pos = self.GetPosition()
            self.center_pos = (pos.x + size.width // 2, pos.y + size.height // 2)

            # Nascondi il cursore
            self.handle_cursor_visibility(False)

            # Cattura il mouse
            self.CaptureMouse()
            self.reset_mouse_position()

            # Aggiorna UI
            if self._debug and self.panel is not None and hasattr(self.panel, 'status_text'):
                self.update_ui(self.panel, "Mouse Capture: ATTIVO", self.panel.status_text.SetLabel)
                self.update_ui(self.panel, wx.Colour(100, 255, 100), self.panel.status_text.SetForegroundColour)

    def disable_mouse_capture(self):
        """
        Disable mouse capture.
        """
        if self.mouse_captured:
            self.mouse_captured = False

            # Rilascia il mouse
            if self.HasCapture():
                self.ReleaseMouse()

            # Ripristina il cursore
            self.handle_cursor_visibility(True)

            # Aggiorna UI
            if self._debug and self.panel is not None and hasattr(self.panel, 'status_text'):
                self.update_ui(self.panel, "Mouse Capture: DISATTIVO", self.panel.status_text.SetLabel)
                self.update_ui(self.panel, wx.Colour(255, 100, 100), self.panel.status_text.SetForegroundColour)

            self.HideOverlay()

    def reset_mouse_position(self):
        """
        Reset mouse position to center.
        """
        if self.mouse_captured and self.center_pos:
            # Sposta il cursore al centro della finestra
            client_center = self.ScreenToClient(self.center_pos)
            self.WarpPointer(client_center.x, client_center.y)

    def on_mouse_move(self, event):
        """
        Handle mouse movement events.
        """
        if not self.mouse_captured:
            event.Skip()
            return

        # Ottieni posizione corrente
        mouse_pos = wx.GetMousePosition()

        # Calcola delta rispetto al centro
        delta_x = mouse_pos.x - self.center_pos[0]
        delta_y = mouse_pos.y - self.center_pos[1]

        # Processa solo se c'è movimento
        if delta_x != 0 or delta_y != 0:
            try:
                self.mouse_conn.send((delta_x, delta_y))
            except Exception as e:
                pass

            # Resetta posizione
            self.reset_mouse_position()

        event.Skip()

    def on_close(self, event):
        """
        Handle window close event.
        """
        self._running = False
        if self.command_timer.IsRunning():
            self.command_timer.Stop()
        self.disable_mouse_capture()
        self.Destroy()

class _CursorHandlerProcess:
    """
    Internal class to run the cursor handler window in a separate process.
    """

    def __init__(self, command_deque, result_deque, mouse_conn: Connection, debug: bool = False, window_class=CursorHandlerWindow):
        self.command_deque = command_deque
        self.result_deque = result_deque
        self.mouse_conn = mouse_conn
        self.window = None
        self.app = None
        self.running = False
        self.window_class = window_class
        self._debug = debug

    def run(self):
        self.app = wx.App()
        self.window = self.window_class(command_deque=self.command_deque, result_deque=self.result_deque,
                                        mouse_conn=self.mouse_conn, debug=self._debug)

        # Notify that the window is ready
        self.result_deque.append({'type': 'window_ready'})
        self.running = True
        self.app.MainLoop()
        self.result_deque.append({'type': 'process_ended'})

class CursorHandlerWorker(object):
    """
    Base class for cursor handler worker.
    Manages the cursor handler window process and communication.
    There is no platform-specific code here, all platform specifics are in the window class.
    Uses Manager().list() as shared deques for inter-process communication without blocking.
    """
    def __init__(self, event_bus: EventBus, stream: Optional[StreamHandler] = None,
                 debug: bool = False, window_class=CursorHandlerWindow):
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

        # Use Manager for shared lists (thread-safe, non-blocking)
        self.manager: SyncManager = Manager()
        self.command_deque = self.manager.list()  # Shared list as deque
        self.result_deque = self.manager.list()   # Shared list as deque

        # Unidirectional pipe for mouse movement
        self.mouse_conn_rec, self.mouse_conn_send = Pipe(duplex=False)
        self.process = None
        self._is_running = False
        self._mouse_data_task = None  # Async task instead of thread

        self._active_client = None

        self.window_class = window_class

        self._logger = get_logger(self.__class__.__name__)

        # Register to active_screen with async callbacks
        self.event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED, callback=self._on_active_screen_changed, priority=True)
        self.event_bus.subscribe(event_type=EventType.CLIENT_DISCONNECTED, callback=self._on_client_disconnected, priority=True)

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent], **kwargs):
        """Async callback for active screen changed"""
        active_screen = data.active_screen if data else None

        if active_screen:
            # Start capture cursor (now fully async)
            await self.enable_capture_async()
            self._active_client = active_screen
        else:
            await self.disable_capture_async()
            self._active_client = None
            # Empty the mouse pipe to avoid stale data
            while self.mouse_conn_rec.poll():
                try:
                    self.mouse_conn_rec.recv()
                except EOFError:
                    break

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent], **kwargs):
        """Async callback for client inactive"""
        if data is None:
            return

        if self._active_client and data.client_screen == self._active_client:
            self._active_client = None
            await self.disable_capture_async()

    def start(self, wait_ready=True, timeout=1) -> bool:
        """Avvia il processo della window"""
        if self._is_running:
            return True

        self.process = Process(target=_CursorHandlerProcess(command_deque=self.command_deque,
                                                            result_deque=self.result_deque,
                                                            mouse_conn=self.mouse_conn_send,
                                                            window_class=self.window_class,
                                                            debug=self._debug).run)
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
            # Aspetta che la window sia pronta (polling non-bloccante)
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    if len(self.result_deque) > 0:
                        result = self.result_deque.pop(0)
                        if result.get('type') == 'window_ready':
                            self._logger.debug("Started")
                            return True
                except (IndexError, AttributeError):
                    pass
                time.sleep(0.01)  # Small sleep to avoid busy waiting
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

        self.send_command({'type': 'quit'})

        if self.process:
            # Run process join in executor to avoid blocking
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.process.join, timeout)
            if self.process.is_alive():
                self.process.terminate()
                await loop.run_in_executor(None, self.process.join, 1)

        # Close connections
        self.mouse_conn_send.close()
        self.mouse_conn_rec.close()

        # Shutdown manager
        try:
            self.manager.shutdown()
        except Exception:
            pass

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

        while self._is_running:
            try:
                # Poll non-bloccante
                has_data = await loop.run_in_executor(None, self.mouse_conn_rec.poll, 0.0000001)

                if has_data:
                    # Leggi dal pipe in executor
                    delta_x, delta_y = await loop.run_in_executor(None, self.mouse_conn_rec.recv) #type: ignore

                    mouse_event = MouseEvent(action=MouseEvent.MOVE_ACTION)
                    mouse_event.dx = delta_x
                    mouse_event.dy = delta_y

                    # Invio async via stream
                    await self.stream.send(mouse_event)
                else:
                    # Piccolo sleep per evitare busy waiting
                    await asyncio.sleep(0)

            except EOFError:
                break
            except Exception as e:
                await asyncio.sleep(0)

    def send_command(self, command: Dict[str, Any]):
        """Invia un comando alla window (non-blocking)"""
        if not self._is_running:
            raise RuntimeError("Window process not running")
        self.command_deque.append(command)

    async def send_command_async(self, command: Dict[str, Any], wait_result: bool = True, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """
        Invia un comando alla window e aspetta il risultato in modo async.

        Args:
            command: Command dictionary to send
            wait_result: If True, wait for result
            timeout: Max time to wait for result

        Returns:
            Result dictionary or None if timeout
        """
        self.send_command(command)

        if not wait_result:
            return None

        # Async polling per risultato
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout:
            try:
                if len(self.result_deque) > 0:
                    result = self.result_deque.pop(0)
                    return result
            except (IndexError, AttributeError):
                pass
            await asyncio.sleep(0.005)  # Poll every 5ms

        return None

    def get_result_sync(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """Riceve un risultato dalla window (sync, non-blocking polling)"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                if len(self.result_deque) > 0:
                    return self.result_deque.pop(0)
            except (IndexError, AttributeError):
                pass
            time.sleep(0.01)
        return None

    def get_all_results(self) -> list:
        """Riceve tutti i risultati disponibili (non-blocking)"""
        results = []
        try:
            while len(self.result_deque) > 0:
                results.append(self.result_deque.pop(0))
        except (IndexError, AttributeError):
            pass
        return results

    # Sync versions (backward compatibility)
    def enable_capture(self):
        """Abilita la cattura del mouse (sync)"""
        self.send_command({'type': 'enable_capture'})
        return self.get_result_sync()

    def disable_capture(self):
        """Disabilita la cattura del mouse (sync)"""
        self.send_command({'type': 'disable_capture'})
        return self.get_result_sync()

    def set_message(self, message: str):
        """Imposta un messaggio nella window (sync)"""
        self.send_command({'type': 'set_message', 'message': message})
        return self.get_result_sync()

    # Async versions (recommended)
    async def enable_capture_async(self) -> Optional[Dict[str, Any]]:
        """Abilita la cattura del mouse (async)"""
        return await self.send_command_async({'type': 'enable_capture'}, wait_result=True, timeout=1.0)

    async def disable_capture_async(self) -> Optional[Dict[str, Any]]:
        """Disabilita la cattura del mouse (async)"""
        return await self.send_command_async({'type': 'disable_capture'}, wait_result=True, timeout=1.0)

    async def set_message_async(self, message: str) -> Optional[Dict[str, Any]]:
        """Imposta un messaggio nella window (async)"""
        return await self.send_command_async({'type': 'set_message', 'message': message}, wait_result=True, timeout=0.5)
