import wx
import time
import threading
from pynput.mouse import Controller


class TestMouseCaptureWindow(wx.Frame):
    def __init__(self):
        super().__init__(None, title="Test Mouse Capture", size=(600, 500))

        self.mouse_captured = False
        self.center_pos = None
        self.total_dx = 0
        self.total_dy = 0

        # Registrazione movimenti
        self.is_recording = False
        self.is_replaying = False
        self.recorded_movements = []
        self.recording_start_time = None

        # Controller mouse per replay
        self.mouse_controller = Controller()

        # Panel principale
        panel = wx.Panel(self)
        panel.SetBackgroundColour(wx.Colour(40, 40, 40))

        # Layout
        vbox = wx.BoxSizer(wx.VERTICAL)

        # Titolo
        title = wx.StaticText(panel, label="Test Mouse Capture (FPS Style)")
        title.SetForegroundColour(wx.WHITE)
        title.SetFont(wx.Font(16, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        vbox.Add(title, 0, wx.ALL | wx.CENTER, 10)

        # Info
        self.info_text = wx.StaticText(panel, label="Premi SPAZIO per attivare/disattivare la cattura")
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
        instructions = wx.StaticText(panel,
                                     label="SPAZIO: Toggle capture | R: Registra | P: Riproduci\nESC: Disattiva | C: Cancella | Q: Esci")
        instructions.SetForegroundColour(wx.Colour(150, 150, 150))
        vbox.Add(instructions, 0, wx.ALL | wx.CENTER, 20)

        panel.SetSizer(vbox)

        # Eventi
        self.Bind(wx.EVT_MOTION, self.on_mouse_move)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key_press)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        self.Centre()
        self.Show()

    def on_key_press(self, event):
        key_code = event.GetKeyCode()

        if key_code == wx.WXK_SPACE:
            if self.mouse_captured:
                self.disable_mouse_capture()
            else:
                self.enable_mouse_capture()
        elif key_code == ord('R') or key_code == ord('r'):
            self.toggle_recording()
        elif key_code == ord('P') or key_code == ord('p'):
            self.replay_movements()
        elif key_code == ord('C') or key_code == ord('c'):
            self.clear_recording()
        elif key_code == wx.WXK_ESCAPE:
            self.disable_mouse_capture()
            self.stop_recording()
        elif key_code == ord('Q') or key_code == ord('q'):
            self.Close()
        else:
            event.Skip()

    def toggle_recording(self):
        if self.is_replaying:
            return

        if not self.mouse_captured:
            self.info_text.SetLabel("Attiva prima la cattura del mouse (SPAZIO)")
            return

        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        self.is_recording = True
        self.recorded_movements = []
        self.recording_start_time = time.time()
        self.recording_text.SetLabel("Registrazione: REC ●")
        self.recording_text.SetForegroundColour(wx.Colour(255, 50, 50))
        self.info_text.SetLabel("Registrazione in corso... Premi R per fermare")

    def stop_recording(self):
        if self.is_recording:
            self.is_recording = False
            self.recording_text.SetLabel(f"Registrazione: OFF ({len(self.recorded_movements)} movimenti)")
            self.recording_text.SetForegroundColour(wx.Colour(150, 150, 150))
            self.info_text.SetLabel("Registrazione completata! Premi P per riprodurre")
            self.record_info.SetLabel(f"Movimenti registrati: {len(self.recorded_movements)}")

    def clear_recording(self):
        self.recorded_movements = []
        self.record_info.SetLabel("Movimenti registrati: 0")
        self.info_text.SetLabel("Registrazione cancellata")

    def replay_movements(self):
        if not self.recorded_movements:
            self.info_text.SetLabel("Nessun movimento registrato!")
            return

        if self.is_recording:
            self.info_text.SetLabel("Ferma prima la registrazione!")
            return

        if self.is_replaying:
            return

        # Disabilita temporaneamente la cattura per vedere il cursore
        was_captured = self.mouse_captured
        if was_captured:
            self.disable_mouse_capture()

        self.is_replaying = True
        self.recording_text.SetLabel("Riproduzione: PLAY ▶")
        self.recording_text.SetForegroundColour(wx.Colour(100, 255, 100))

        # Avvia il replay in un thread separato
        replay_thread = threading.Thread(target=self._replay_thread, args=(was_captured,), daemon=True)
        replay_thread.start()

    def _replay_thread(self, restore_capture):
        start_time = time.time()

        for timestamp, dx, dy in self.recorded_movements:
            if not self.is_replaying:
                break

            # Aspetta fino al momento giusto
            target_time = start_time + timestamp
            sleep_time = target_time - time.time()
            if sleep_time > 0:
                time.sleep(sleep_time)

            # Muovi effettivamente il cursore
            current_pos = self.mouse_controller.position
            self.mouse_controller.position = (current_pos[0] + dx, current_pos[1] + dy)

            # Aggiorna UI nel thread principale
            wx.CallAfter(self._apply_movement_delta, dx, dy)

        wx.CallAfter(self._finish_replay, restore_capture)

    def _apply_movement_delta(self, dx, dy):
        self.total_dx += dx
        self.total_dy += dy
        self.delta_text.SetLabel(f"Delta X: {dx:4d}, Delta Y: {dy:4d}")
        self.total_text.SetLabel(f"Totale X: {self.total_dx:6d}, Totale Y: {self.total_dy:6d}")

    def _finish_replay(self, restore_capture):
        self.is_replaying = False
        self.recording_text.SetLabel(f"Registrazione: OFF ({len(self.recorded_movements)} movimenti)")
        self.recording_text.SetForegroundColour(wx.Colour(150, 150, 150))
        self.info_text.SetLabel("Riproduzione completata!")

        # Ripristina la cattura se era attiva prima del replay
        if restore_capture:
            wx.CallLater(500, self.enable_mouse_capture)

    def enable_mouse_capture(self):
        if not self.mouse_captured:
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

    def reset_mouse_position(self):
        if self.mouse_captured and self.center_pos:
            # Sposta il cursore al centro della finestra
            client_center = self.ScreenToClient(self.center_pos)
            self.WarpPointer(client_center.x, client_center.y)

    def on_mouse_move(self, event):
        if not self.mouse_captured or self.is_replaying:
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

            # Resetta posizione
            wx.CallAfter(self.reset_mouse_position)

        event.Skip()

    def on_close(self, event):
        self.is_replaying = False
        self.disable_mouse_capture()
        self.Destroy()


if __name__ == "__main__":
    app = wx.App()
    frame = TestMouseCaptureWindow()
    app.MainLoop()