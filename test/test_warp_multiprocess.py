import wx
import time
import multiprocessing as mp
import sys
from queue import Empty
from pynput.mouse import Controller


class TestMouseCaptureWindow(wx.Frame):
    """Window che può essere controllata tramite comandi da un processo esterno"""

    def __init__(self, command_queue, result_queue):
        super().__init__(None, title="Test Mouse Capture (Multiprocess)", size=(600, 500))

        self.command_queue = command_queue
        self.result_queue = result_queue

        self.mouse_captured = False
        self.center_pos = None
        self.total_dx = 0
        self.total_dy = 0

        # Registrazione movimenti
        self.is_recording = False
        self.is_replaying = False
        self.recorded_movements = []
        self.recording_start_time = None

        # Controller mouse
        self.mouse_controller = Controller()

        # Setup UI
        self._setup_ui()

        # Timer per processare comandi dalla queue
        self.command_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._process_commands, self.command_timer)
        self.command_timer.Start(50)  # Check ogni 50ms

        self.Centre()
        self.Show()

    def _setup_ui(self):
        """Configura l'interfaccia utente"""
        panel = wx.Panel(self)
        panel.SetBackgroundColour(wx.Colour(40, 40, 40))

        vbox = wx.BoxSizer(wx.VERTICAL)

        # Titolo
        title = wx.StaticText(panel, label="Test Mouse Capture (Multiprocess)")
        title.SetForegroundColour(wx.WHITE)
        title.SetFont(wx.Font(16, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        vbox.Add(title, 0, wx.ALL | wx.CENTER, 10)

        # Info
        self.info_text = wx.StaticText(panel, label="Controllato da processo esterno")
        self.info_text.SetForegroundColour(wx.Colour(200, 200, 200))
        vbox.Add(self.info_text, 0, wx.ALL | wx.CENTER, 5)

        # Stato
        self.status_text = wx.StaticText(panel, label="Mouse Capture: DISATTIVO")
        self.status_text.SetForegroundColour(wx.Colour(255, 100, 100))
        self.status_text.SetFont(wx.Font(12, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        vbox.Add(self.status_text, 0, wx.ALL | wx.CENTER, 10)

        # Stato registrazione
        self.recording_text = wx.StaticText(panel, label="Registrazione: OFF")
        self.recording_text.SetForegroundColour(wx.Colour(150, 150, 150))
        self.recording_text.SetFont(wx.Font(11, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        vbox.Add(self.recording_text, 0, wx.ALL | wx.CENTER, 5)

        # Delta display
        self.delta_text = wx.StaticText(panel, label="Delta X: 0, Delta Y: 0")
        self.delta_text.SetForegroundColour(wx.WHITE)
        self.delta_text.SetFont(wx.Font(10, wx.FONTFAMILY_MODERN, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        vbox.Add(self.delta_text, 0, wx.ALL | wx.CENTER, 5)

        # Totale movimento
        self.total_text = wx.StaticText(panel, label="Totale X: 0, Totale Y: 0")
        self.total_text.SetForegroundColour(wx.Colour(150, 200, 255))
        self.total_text.SetFont(wx.Font(10, wx.FONTFAMILY_MODERN, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        vbox.Add(self.total_text, 0, wx.ALL | wx.CENTER, 5)

        # Info registrazione
        self.record_info = wx.StaticText(panel, label="Movimenti registrati: 0")
        self.record_info.SetForegroundColour(wx.Colour(255, 200, 100))
        vbox.Add(self.record_info, 0, wx.ALL | wx.CENTER, 5)

        # Istruzioni
        instructions = wx.StaticText(panel, label="Comandi inviati dal processo padre")
        instructions.SetForegroundColour(wx.Colour(150, 150, 150))
        vbox.Add(instructions, 0, wx.ALL | wx.CENTER, 20)

        panel.SetSizer(vbox)

        # Eventi
        self.Bind(wx.EVT_MOTION, self.on_mouse_move)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        # Forza il focus della window (importante per macOS)
        self.Raise()
        self.SetFocus()
        self.RequestUserAttention()

    def _process_commands(self, event):
        """Processa i comandi dalla queue"""
        try:
            while True:
                try:
                    command = self.command_queue.get_nowait()
                    self._execute_command(command)
                except Empty:
                    break
        except Exception as e:
            print(f"Error processing commands: {e}")

    def _execute_command(self, command):
        """Esegue un comando ricevuto"""
        cmd_type = command.get('type')

        if cmd_type == 'enable_capture':
            self.enable_mouse_capture()
            self.result_queue.put({'type': 'capture_enabled', 'success': True})

        elif cmd_type == 'disable_capture':
            self.disable_mouse_capture()
            self.result_queue.put({'type': 'capture_disabled', 'success': True})

        elif cmd_type == 'start_recording':
            self.start_recording()
            self.result_queue.put({'type': 'recording_started', 'success': True})

        elif cmd_type == 'stop_recording':
            self.stop_recording()
            self.result_queue.put({
                'type': 'recording_stopped',
                'success': True,
                'movements': list(self.recorded_movements)
            })

        elif cmd_type == 'clear_recording':
            self.clear_recording()
            self.result_queue.put({'type': 'recording_cleared', 'success': True})

        elif cmd_type == 'get_stats':
            self.result_queue.put({
                'type': 'stats',
                'total_dx': self.total_dx,
                'total_dy': self.total_dy,
                'is_captured': self.mouse_captured,
                'is_recording': self.is_recording,
                'movement_count': len(self.recorded_movements)
            })

        elif cmd_type == 'set_message':
            message = command.get('message', '')
            self.info_text.SetLabel(message)
            self.result_queue.put({'type': 'message_set', 'success': True})

        elif cmd_type == 'quit':
            self.Close()

    def enable_mouse_capture(self):
        """Abilita la cattura del mouse"""
        if not self.mouse_captured:
            # Forza il focus prima di catturare
            self.Raise()
            self.SetFocus()

            self.mouse_captured = True

            # Calcola il centro della finestra
            size = self.GetSize()
            pos = self.GetPosition()
            self.center_pos = (pos.x + size.width // 2, pos.y + size.height // 2)

            # Nascondi il cursore
            cursor = wx.Cursor(wx.CURSOR_BLANK)
            self.SetCursor(cursor)

            # Cattura il mouse
            self.CaptureMouse()
            self.reset_mouse_position()

            # Aggiorna UI
            self.status_text.SetLabel("Mouse Capture: ATTIVO")
            self.status_text.SetForegroundColour(wx.Colour(100, 255, 100))
            self.total_dx = 0
            self.total_dy = 0

    def disable_mouse_capture(self):
        """Disabilita la cattura del mouse"""
        if self.mouse_captured:
            self.mouse_captured = False
            self.stop_recording()

            # Rilascia il mouse
            if self.HasCapture():
                self.ReleaseMouse()

            # Ripristina il cursore
            self.SetCursor(wx.NullCursor)

            # Aggiorna UI
            self.status_text.SetLabel("Mouse Capture: DISATTIVO")
            self.status_text.SetForegroundColour(wx.Colour(255, 100, 100))

    def start_recording(self):
        """Inizia la registrazione dei movimenti"""
        if not self.mouse_captured:
            return

        self.is_recording = True
        self.recorded_movements = []
        self.recording_start_time = time.time()
        self.recording_text.SetLabel("Registrazione: REC ●")
        self.recording_text.SetForegroundColour(wx.Colour(255, 50, 50))

    def stop_recording(self):
        """Ferma la registrazione dei movimenti"""
        if self.is_recording:
            self.is_recording = False
            self.recording_text.SetLabel(f"Registrazione: OFF ({len(self.recorded_movements)} movimenti)")
            self.recording_text.SetForegroundColour(wx.Colour(150, 150, 150))
            self.record_info.SetLabel(f"Movimenti registrati: {len(self.recorded_movements)}")

    def clear_recording(self):
        """Cancella la registrazione"""
        self.recorded_movements = []
        self.record_info.SetLabel("Movimenti registrati: 0")

    def reset_mouse_position(self):
        """Resetta la posizione del mouse al centro"""
        if self.mouse_captured and self.center_pos:
            client_center = self.ScreenToClient(self.center_pos)
            self.WarpPointer(client_center.x, client_center.y)

    def on_mouse_move(self, event):
        """Gestisce i movimenti del mouse"""
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
            # Registra il movimento se in modalità registrazione
            if self.is_recording:
                elapsed = time.time() - self.recording_start_time
                self.recorded_movements.append((elapsed, delta_x, delta_y))
                self.record_info.SetLabel(f"Movimenti registrati: {len(self.recorded_movements)}")

            self.total_dx += delta_x
            self.total_dy += delta_y

            # Aggiorna UI
            self.delta_text.SetLabel(f"Delta X: {delta_x:4d}, Delta Y: {delta_y:4d}")
            self.total_text.SetLabel(f"Totale X: {self.total_dx:6d}, Totale Y: {self.total_dy:6d}")

            # Notifica il processo padre del movimento
            try:
                self.result_queue.put({
                    'type': 'mouse_move',
                    'dx': delta_x,
                    'dy': delta_y,
                    'total_dx': self.total_dx,
                    'total_dy': self.total_dy
                }, block=False)
            except:
                pass  # Queue piena, ignora

            # Resetta posizione
            wx.CallAfter(self.reset_mouse_position)

        event.Skip()

    def on_close(self, event):
        """Gestisce la chiusura della finestra"""
        self.command_timer.Stop()
        self.disable_mouse_capture()
        self.result_queue.put({'type': 'window_closed'})
        self.Destroy()


def window_process(command_queue, result_queue):
    """Funzione che esegue nel processo separato"""
    app = wx.App()
    frame = TestMouseCaptureWindow(command_queue, result_queue)
    result_queue.put({'type': 'window_ready'})
    app.MainLoop()
    result_queue.put({'type': 'process_ended'})


class MouseCaptureWindowController:
    """Controller per gestire la window da un processo padre"""

    def __init__(self):
        self.command_queue = mp.Queue()
        self.result_queue = mp.Queue()
        self.process = None
        self.is_running = False

    def start(self, wait_ready=True, timeout=5):
        """Avvia il processo della window"""
        if self.is_running:
            return

        self.process = mp.Process(
            target=window_process,
            args=(self.command_queue, self.result_queue),
            daemon=True
        )
        self.process.start()
        self.is_running = True

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

        self.is_running = False

    def send_command(self, command):
        """Invia un comando alla window"""
        if not self.is_running:
            raise RuntimeError("Window process not running")
        self.command_queue.put(command)

    def get_result(self, timeout=1):
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

    def start_recording(self):
        """Inizia la registrazione"""
        self.send_command({'type': 'start_recording'})
        return self.get_result()

    def stop_recording(self):
        """Ferma la registrazione"""
        self.send_command({'type': 'stop_recording'})
        return self.get_result()

    def clear_recording(self):
        """Cancella la registrazione"""
        self.send_command({'type': 'clear_recording'})
        return self.get_result()

    def get_stats(self):
        """Ottiene le statistiche"""
        self.send_command({'type': 'get_stats'})
        return self.get_result()

    def set_message(self, message):
        """Imposta un messaggio nella window"""
        self.send_command({'type': 'set_message', 'message': message})
        return self.get_result()

    def collect_mouse_events(self, duration=1.0):
        """Raccoglie gli eventi mouse per una durata specificata"""
        events = []
        start_time = time.time()

        while time.time() - start_time < duration:
            try:
                result = self.result_queue.get(timeout=0.01)
                if result.get('type') == 'mouse_move':
                    events.append(result)
            except Empty:
                continue

        return events


if __name__ == "__main__":
    # Esempio di utilizzo
    print("Avvio controller della window...")
    controller = MouseCaptureWindowController()

    try:
        # Avvia la window
        controller.start()
        print("Window avviata!")

        # Imposta un messaggio
        time.sleep(0.5)
        controller.set_message("Abilitazione cattura tra 2 secondi...")
        time.sleep(2)

        # Abilita la cattura
        print("Abilitazione cattura mouse...")
        result = controller.enable_capture()
        print(f"Risultato: {result}")

        # Aspetta un po'
        time.sleep(1)

        # Inizia la registrazione
        print("Inizio registrazione...")
        controller.start_recording()
        controller.set_message("REGISTRAZIONE IN CORSO - Muovi il mouse!")

        # Raccoglie eventi per 5 secondi
        print("Raccolta eventi mouse per 5 secondi...")
        events = controller.collect_mouse_events(duration=5.0)
        print(f"Eventi raccolti: {len(events)}")

        # Ferma la registrazione
        print("Arresto registrazione...")
        result = controller.stop_recording()
        print(f"Movimenti registrati: {len(result.get('movements', []))}")

        # Ottieni statistiche
        time.sleep(0.5)
        stats = controller.get_stats()
        print(f"Statistiche finali: {stats}")

        # Aspetta un po' prima di chiudere
        controller.set_message("Chiusura tra 2 secondi...")
        time.sleep(2)

    finally:
        # Ferma la window
        print("Chiusura window...")
        controller.stop()
        print("Test completato!")

