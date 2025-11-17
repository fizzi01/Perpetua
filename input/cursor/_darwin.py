"""
Logic to handle cursor visibility on macOS systems.
"""
from queue import Empty
from typing import Optional

import wx
import time
import threading

from multiprocessing import Queue, Pipe, Process
from multiprocessing.connection import Connection

# Object-c Library
import objc
import Quartz

from Quartz import kCGMaximumWindowLevel


from AppKit import (
    NSCursor,
    NSApplication,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSScreenSaverWindowLevel,
    NSApplicationActivationPolicyAccessory,
    NSWorkspace,
    NSApplicationActivateIgnoringOtherApps,
    NSApplicationPresentationAutoHideDock,
    NSApplicationPresentationAutoHideMenuBar,
    NSWindowCollectionBehaviorParticipatesInCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorMoveToActiveSpace
)

# Accessibility API
from ApplicationServices import (
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXUIElementSetAttributeValue,
    kAXWindowsAttribute
)

from event import EventType, MouseEvent
from event.EventBus import EventBus
from network.stream.GenericStream import StreamHandler

from . import _base


class OverlayPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)

        # Layout
        vbox = wx.BoxSizer(wx.VERTICAL)

        # Titolo
        title = wx.StaticText(self, label="Test Mouse Capture Window")
        title.SetForegroundColour(wx.WHITE)
        title.SetFont(wx.Font(16, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        vbox.Add(title, 0, wx.ALL | wx.CENTER, 10)

        # Info
        self.info_text = wx.StaticText(self, label="Premi SPAZIO per attivare/disattivare la cattura")
        self.info_text.SetForegroundColour(wx.Colour(200, 200, 200))
        vbox.Add(self.info_text, 0, wx.ALL | wx.CENTER, 5)

        # Stato
        self.status_text = wx.StaticText(self, label="Mouse Capture: DISATTIVO")
        self.status_text.SetForegroundColour(wx.Colour(255, 100, 100))
        self.status_text.SetFont(wx.Font(12, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        vbox.Add(self.status_text, 0, wx.ALL | wx.CENTER, 10)


        # Delta display
        self.delta_text = wx.StaticText(self, label="Delta X: 0, Delta Y: 0")
        self.delta_text.SetForegroundColour(wx.WHITE)
        self.delta_text.SetFont(wx.Font(10, wx.FONTFAMILY_MODERN, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        vbox.Add(self.delta_text, 0, wx.ALL | wx.CENTER, 5)

        # Istruzioni
        instructions = wx.StaticText(self,
                                     label="SPAZIO: Toggle capture\nESC: Disattiva | Q: Esci")
        instructions.SetForegroundColour(wx.Colour(150, 150, 150))
        vbox.Add(instructions, 0, wx.ALL | wx.CENTER, 20)

        # Obtain NSApplication instance
        NSApp = NSApplication.sharedApplication()
        # Set activation policy to Accessory to hide the icon in the Dock
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        self.SetSizer(vbox)

        # Black background
        self.SetBackgroundColour(wx.Colour(10, 10, 10))

class CursorHandlerWindow(wx.Frame):
    def __init__(self, command_queue: Queue, result_queue:  Queue, mouse_conn: Connection, debug: bool = False):
        super().__init__(None, title="", size=(400, 400))


        self._debug = debug
        self.mouse_captured = False
        self.center_pos = None

        self.command_queue: Queue = command_queue
        self.result_queue: Queue = result_queue
        self.mouse_conn: Connection = mouse_conn

        self.previous_app = None
        self.previous_app_pid = None

        # Start command processing thread
        self._running = True
        self.command_thread = threading.Thread(target=self._process_commands, daemon=True)
        self.command_thread.start()

        # Panel principale
        self.panel = OverlayPanel(self)

        # Obtain NSApplication instance
        NSApp = NSApplication.sharedApplication()
        # Set activation policy to Accessory to hide the icon in the Dock
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        if not self._debug:
            self.SetTransparent(0)

        # Eventi
        self.Bind(wx.EVT_MOTION, self.on_mouse_move)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key_press)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        self.Centre()
        self.Show()

        # Forza il focus della window (importante per macOS)
        # self.Raise()
        # self.SetFocus()
        # self.RequestUserAttention()

        # Porta la finestra in overlay
        #self.ForceOverlay()
        self.HideOverlay()

    def _process_commands(self):
        """Processa i comandi dalla queue"""
        try:
            while self._running:
                try:
                    command = self.command_queue.get(timeout=0.2)
                    self._execute_command(command)
                except Empty:
                    continue
        except Exception as e:
            print(f"Error processing commands: {e}")

    def _execute_command(self, command):
        """Esegue un comando ricevuto"""
        cmd_type = command.get('type')

        if cmd_type == 'enable_capture':
            wx.CallAfter(self.enable_mouse_capture)
            self.result_queue.put({'type': 'capture_enabled', 'success': True})

        elif cmd_type == 'disable_capture':
            wx.CallAfter(self.disable_mouse_capture)
            self.result_queue.put({'type': 'capture_disabled', 'success': True})
        elif cmd_type == 'get_stats':
            self.result_queue.put({
                'type': 'stats',
                'is_captured': self.mouse_captured,
            })

        elif cmd_type == 'set_message':
            message = command.get('message', '')
            self.panel.info_text.SetLabel(message)
            self.result_queue.put({'type': 'message_set', 'success': True})

        elif cmd_type == 'quit':
            self._running = False
            self.Close()

    def ForceOverlay(self):
        try:
            self.SetSize(400, 400)
            self.Show(True)

            self.previous_app = NSWorkspace.sharedWorkspace().frontmostApplication()
            self.previous_app_pid = self.previous_app.processIdentifier()

            NSApp = NSApplication.sharedApplication()
            NSApp.setPresentationOptions_(
                NSApplicationPresentationAutoHideDock | NSApplicationPresentationAutoHideMenuBar)
            NSApp.activateIgnoringOtherApps_(True)

            window_ptr = self.GetHandle()

            ns_view = objc.objc_object(c_void_p=window_ptr) #type: ignore
            ns_window = ns_view.window()
            ns_window.setLevel_(kCGMaximumWindowLevel + 1)
            ns_window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary | NSWindowCollectionBehaviorStationary)
            ns_window.setIgnoresMouseEvents_(False)
            ns_window.makeKeyAndOrderFront_(None)
        except Exception as e:
            print(f"Error forcing overlay: {e}")

    def HideOverlay(self):
        try:
            NSApp = NSApplication.sharedApplication()
            NSApp.setPresentationOptions_(0)
            NSApp.activateIgnoringOtherApps_(False)

            window_ptr = self.GetHandle()
            ns_view = objc.objc_object(c_void_p=window_ptr) #type: ignore
            ns_window = ns_view.window()
            ns_window.setLevel_(NSScreenSaverWindowLevel - 1)
            ns_window.setIgnoresMouseEvents_(False)
            ns_window.setCollectionBehavior_(0)

            self.RestorePreviousApp()
            #self.panel.Hide()
            self.Hide()
            # Resize to 0x0 to avoid interaction
            self.SetSize((0, 0))
        except Exception as e:
            print(f"Error hiding overlay: {e}")

    def RestorePreviousApp(self):
        try:
            if self.previous_app:
                self.previous_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            self.previous_app = None
            self.previous_app_pid = None
        except Exception as e:
            print(f"Error restoring previous app: {e}")

    def on_key_press(self, event):
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

    def enable_mouse_capture(self):
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
            cursor = wx.Cursor(wx.CURSOR_BLANK)
            self.SetCursor(cursor)
            Quartz.CGDisplayHideCursor(Quartz.CGMainDisplayID())

            # Cattura il mouse
            self.CaptureMouse()
            self.reset_mouse_position()

            # Aggiorna UI
            if self._debug:
                self.panel.status_text.SetLabel("Mouse Capture: ATTIVO")
                self.panel.status_text.SetForegroundColour(wx.Colour(100, 255, 100))

    def disable_mouse_capture(self):
        if self.mouse_captured:
            self.mouse_captured = False

            # Rilascia il mouse
            if self.HasCapture():
                self.ReleaseMouse()

            # Ripristina il cursore
            self.SetCursor(wx.NullCursor)
            Quartz.CGDisplayShowCursor(Quartz.CGMainDisplayID())

            # Aggiorna UI
            if self._debug:
                self.panel.status_text.SetLabel("Mouse Capture: DISATTIVO")
                self.panel.status_text.SetForegroundColour(wx.Colour(255, 100, 100))

            self.HideOverlay()

    def reset_mouse_position(self):
        if self.mouse_captured and self.center_pos:
            # Sposta il cursore al centro della finestra
            client_center = self.ScreenToClient(self.center_pos)
            self.WarpPointer(client_center.x, client_center.y)

    def on_mouse_move(self, event):
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
            # Aggiorna UI
            if self._debug:
                self.panel.delta_text.SetLabel(f"Delta X: {delta_x:4d}, Delta Y: {delta_y:4d}")
            #timestamp = time.time()
            #print(f"[{timestamp}] Mouse moved: ΔX={delta_x}, ΔY={delta_y}")
            try:
                self.mouse_conn.send((delta_x, delta_y))
            except Exception as e:
                pass

            # Resetta posizione
            wx.CallAfter(self.reset_mouse_position)

        event.Skip()

    def on_close(self, event):
        self._running = False
        self.disable_mouse_capture()
        self.Destroy()

class CursorHandlerProcess:

    def __init__(self, command_queue, result_queue, mouse_conn: Connection, debug: bool = False):
        self.command_queue = command_queue
        self.result_queue = result_queue
        self.mouse_conn = mouse_conn
        self.window = None
        self.app = None
        self.running = False

        self._debug = debug

    def run(self):
        self.app = wx.App()
        self.window = CursorHandlerWindow(command_queue=self.command_queue,result_queue=self.result_queue,mouse_conn=self.mouse_conn, debug=self._debug)

        # Notify that the window is ready
        self.result_queue.put({'type': 'window_ready'})
        self.running = True
        self.app.MainLoop()
        self.result_queue.put({'type': 'process_ended'})

class CursorHandlerWorker(_base.CursorHandlerWorker):

    def __init__(self, event_bus: EventBus, stream: Optional[StreamHandler] = None, debug: bool = False):
        self.event_bus = event_bus
        self.stream = stream

        self._debug = debug

        self.command_queue = Queue()
        self.result_queue = Queue()

        # Unidirectional pipe for mouse movement
        self.mouse_conn_rec, self.mouse_conn_send = Pipe(duplex=False)

        self.process = None
        self.is_running = False
        self._moue_data_thread = None

        # Register to active_screen
        self.event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED, callback=self._on_active_screen_changed)
        self.event_bus.subscribe(event_type=EventType.CLIENT_ACTIVE, callback=self._on_client_active)
        self.event_bus.subscribe(event_type=EventType.CLIENT_INACTIVE, callback=self._on_client_inactive)

    def _on_active_screen_changed(self, data):
        active_screen = data.get("active_screen")

        if active_screen:
            # Start capture cursor
            self.enable_capture()
        else:
            self.disable_capture()

    def _on_client_active(self, data: dict):
        self.disable_capture()

    def _on_client_inactive(self, data: dict):
        self.enable_capture()

    def start(self, wait_ready=True, timeout=1):
        """Avvia il processo della window"""
        if self.is_running:
            return True

        self.process = Process(target=CursorHandlerProcess(command_queue=self.command_queue,
                                                           result_queue=self.result_queue,
                                                           mouse_conn=self.mouse_conn_send, debug=self._debug).run)
        self.process.start()
        self.is_running = True

        if self.stream is not None:
            self._moue_data_thread = threading.Thread(target=self._mouse_data_listener)
            self._moue_data_thread.start()

        if wait_ready:
            # Aspetta che la window sia pronta
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    result = self.result_queue.get(timeout=0.1)
                    if result.get('type') == 'window_ready':
                        return True
                except Empty:
                    continue
            raise TimeoutError("Window not ready in time")

        return True

    def stop(self, timeout=2):
        """Ferma il processo della window"""
        if not self.is_running:
            return

        self.send_command({'type': 'quit'})

        if self.process:
            self.process.join(timeout=timeout)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=1)

        if self._moue_data_thread:
            self._moue_data_thread.join(timeout=timeout)

        # Close queues
        self.command_queue.close()
        self.result_queue.close()

        self.mouse_conn_send.close()
        self.mouse_conn_rec.close()

        self.is_running = False

    def _mouse_data_listener(self):
        """Thread per ascoltare i dati del mouse"""
        mouse_event = MouseEvent(action=MouseEvent.MOVE_ACTION)
        while self.is_running:
            try:
                if self.mouse_conn_rec.poll(0.5):
                    delta_x, delta_y = self.mouse_conn_rec.recv()
                    mouse_event.dx = delta_x
                    mouse_event.dy = delta_y
                    self.stream.send(mouse_event)
            except EOFError:
                break
            except Exception as e:
                pass

    def send_command(self, command):
        """Invia un comando alla window"""
        if not self.is_running:
            raise RuntimeError("Window process not running")
        self.command_queue.put(command)

    def get_result(self, timeout: float = 1.0):
        """Riceve un risultato dalla window"""
        try:
            return self.result_queue.get(timeout=timeout)
        except Empty:
            return None

    def get_all_results(self, timeout=0.1):
        """Riceve tutti i risultati disponibili"""
        results = []
        while True:
            result = self.get_result(timeout=timeout)
            if result is None:
                break
            results.append(result)
        return results

    def enable_capture(self):
        """Abilita la cattura del mouse"""
        self.send_command({'type': 'enable_capture'})
        return self.get_result()

    def disable_capture(self):
        """Disabilita la cattura del mouse"""
        self.send_command({'type': 'disable_capture'})
        return self.get_result()

    def set_message(self, message):
        """Imposta un messaggio nella window"""
        self.send_command({'type': 'set_message', 'message': message})
        return self.get_result()


if __name__ == "__main__":
    eb = EventBus()
    controller = CursorHandlerWorker(eb)
    controller.start()

    try:
        # Avvia la window
        controller.start()
        print("Window avviata!")

        # Cycle to enable again and disable
        for i in range(2):
            print(f"Ciclo {i+1}: Abilitazione cattura mouse...")
            result = controller.enable_capture()
            print(f"Risultato: {result}")
            time.sleep(2)
            print(f"Ciclo {i+1}: Disabilitazione cattura mouse...")
            result = controller.disable_capture()
            print(f"Risultato: {result}")
            time.sleep(2)

        # Aspetta un po' prima di chiudere
        controller.set_message("Chiusura tra 2 secondi...")
        time.sleep(2)

    finally:
        # Ferma la window
        print("Chiusura window...")
        controller.stop()
        print("Test completato!")
