import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog
from main import run_server, run_client
from utils import net
import platform as _platform

class PositionDialog(simpledialog.Dialog):
    def __init__(self, master, title="", positions=None, ips=None):
        self.entries = None
        self.ips = None
        self.positions = None
        self.ip_result = None
        self.pos_result = None

        self.checkboxes = None

        self.current_positions = positions if positions else {"left": False, "right": False, "up": False, "down": False}
        self.current_ips = ips if ips else {"left": "", "right": "", "up": "", "down": ""}
        super().__init__(master, title=title)

    def body(self, master):
        self.positions = {"left": tk.BooleanVar(value=self.current_positions.get("left", False)),
                          "right": tk.BooleanVar(value=self.current_positions.get("right", False)),
                          "up": tk.BooleanVar(value=self.current_positions.get("up", False)),
                          "down": tk.BooleanVar(value=self.current_positions.get("down", False))}

        self.ips = {"left": tk.StringVar(value=self.current_ips.get("left", "")),
                    "right": tk.StringVar(value=self.current_ips.get("right", "")),
                    "up": tk.StringVar(value=self.current_ips.get("up", "")),
                    "down": tk.StringVar(value=self.current_ips.get("down", ""))}

        self.checkboxes = {}
        self.entries = {}

        host_frame = tk.Frame(master)
        host_frame.grid(row=1, column=1, pady=10)

        # Creating checkboxes and entries for each position
        self.checkboxes["up"] = tk.Checkbutton(master, text="Up", var=self.positions["up"])
        self.checkboxes["up"].grid(row=0, column=1, pady=(10, 0))
        self.entries["up"] = tk.Entry(master, textvariable=self.ips["up"])
        self.entries["up"].grid(row=1, column=1)

        self.checkboxes["left"] = tk.Checkbutton(master, text="Left", var=self.positions["left"])
        self.checkboxes["left"].grid(row=1, column=0, pady=(10, 0))
        self.entries["left"] = tk.Entry(master, textvariable=self.ips["left"])
        self.entries["left"].grid(row=2, column=0)

        tk.Label(master, text="Host", relief=tk.RAISED).grid(row=2, column=1, pady=(15, 10)   )

        self.checkboxes["right"] = tk.Checkbutton(master, text="Right", var=self.positions["right"])
        self.checkboxes["right"].grid(row=1, column=2, pady=(10, 0))
        self.entries["right"] = tk.Entry(master, textvariable=self.ips["right"])
        self.entries["right"].grid(row=2, column=2)

        self.checkboxes["down"] = tk.Checkbutton(master, text="Down", var=self.positions["down"])
        self.checkboxes["down"].grid(row=3, column=1, pady=(10, 0))
        self.entries["down"] = tk.Entry(master, textvariable=self.ips["down"])
        self.entries["down"].grid(row=4, column=1)

        return master  # return the widget that should have initial focus

    def apply(self):
        self.pos_result = {pos: var.get() for pos, var in self.positions.items()}
        self.ip_result = {pos: ip.get() for pos, ip in self.ips.items() if self.positions[pos].get()}


class ServerConfigGUI:
    def __init__(self, master):

        self.client = None
        self.server = None

        self.master = master
        self.master.resizable(False, False)
        self.master.title("Server Configuration")

        # Variables for server configuration
        self.host = tk.StringVar(value=net.get_local_ip())
        self.port = tk.IntVar(value=5001)
        self.positions = {"left": True, "right": False, "up": False, "down": False}  # default values
        self.ips = {"left": "", "right": "", "up": "", "down": ""}  # default values
        self.logging = tk.BooleanVar(value=True)

        self.process = None

        # Layout configuration
        self.start_button = None
        self.stop_button = None
        self.output = None

        # Variabili per la configurazione del client
        self.client_host = tk.StringVar(value="127.0.0.1")
        self.client_port = tk.IntVar(value=5001)
        self.client_connected = False
        self.connect_button = None
        self.stop_client_button = None

        self.server_widgets = []
        self.client_widgets = []
        # Crea i widget della GUI Server
        self.create_widgets()

        # Aggiungi alla GUI i componenti del client
        self.create_client_widgets()

    def create_widgets(self):
        # Label and entry for host
        label_host = tk.Label(self.master, text="Host:")
        label_host.grid(row=0, column=0, sticky="w")
        self.server_widgets.append(label_host)

        entry_host = tk.Entry(self.master, textvariable=self.host, state="readonly", font=("Helvetica", 10, "bold"))
        entry_host.grid(row=0, column=1, sticky="ew")

        # Label and entry for port
        label_port = tk.Label(self.master, text="Port:")
        label_port.grid(row=1, column=0, sticky="w")
        self.server_widgets.append(label_port)

        entry_port = tk.Entry(self.master, textvariable=self.port)
        entry_port.grid(row=1, column=1, sticky="ew")
        self.server_widgets.append(entry_port)

        # Button to configure positions
        configure_button = tk.Button(self.master, text="Configure Client Positions", command=self.configure_positions)
        configure_button.grid(row=2, column=1, sticky="ew")
        self.server_widgets.append(configure_button)

        # Checkbox for logging
        logging_checkbox = tk.Checkbutton(self.master, text="Enable Logging", variable=self.logging)
        logging_checkbox.grid(row=3, columnspan=2, sticky="w")
        self.server_widgets.append(logging_checkbox)

        # Start server button

        # Button to start the server
        self.start_button = tk.Button(self.master, text="Start Server", command=self.start_server)
        self.start_button.grid(row=4, column=0, sticky="ew")
        self.server_widgets.append(self.start_button)

        # Button to stop the server
        self.stop_button = tk.Button(self.master, text="Stop Server", command=self.stop_server)
        self.stop_button.grid(row=4, column=1, sticky="ew")
        self.stop_button.grid_remove()  # Hide the stop button initially
        self.server_widgets.append(self.stop_button)

        # ScrolledText for output
        self.output = scrolledtext.ScrolledText(self.master, height=10, state="disabled")
        self.output.grid(row=5, columnspan=2, sticky="nsew")

        tk.Label(self.master, text="").grid(row=6)  # Spacer

    def start_server(self):
        # Extract values from GUI
        host = self.host.get()
        port = self.port.get()
        ips = self.ips
        position = self.get_active_positions()
        logging = self.logging.get()

        try:
            # Start the server
            self.server = run_server(host=host,port=port, pos=position, ips=ips,logging=logging,wait= 5, screen_threshold= 5,root= self.master,stdout= self.update_output)

            # Update button visibility
            self.start_button.grid_remove()
            self.stop_button.grid()
            self.update_client_widgets_state(disabled=True)
        except Exception as e:
            self.update_output(f"Failed to start server: {e}")

    def update_output(self, message):
        # Insert message to the scrolled text widget
        if self.output.winfo_exists():  # Check if widget still exists
            self.output.config(state='normal')
            self.output.insert(tk.END, message + "\n")
            self.output.see(tk.END)
            self.output.config(state='disabled')

    def configure_positions(self):
        dialog = PositionDialog(self.master, title="Configure Positions", positions=self.positions, ips=self.ips)
        if dialog.pos_result and dialog.ip_result:
            self.positions = dialog.pos_result  # Update positions based on dialog
            self.ips = dialog.ip_result  # Update IPs based on dialog
            print(self.positions)
            print(self.ips)

    def get_active_positions(self):
        # Create a list of positions that are set to True
        active_positions = [position for position, is_active in self.positions.items() if is_active]
        # Join the list into a comma-separated string
        positions_string = ",".join(active_positions)
        return positions_string

    def stop_server(self):
        # Method to stop the server process
        if self.server:
            if self.server.stop():
                self.stop_button.grid_remove()
                self.start_button.grid()
                self.update_client_widgets_state(disabled=False)

    def create_client_widgets(self):
        # Sezione Client
        tk.Label(self.master, text="Client Configuration").grid(row=7, columnspan=2, sticky="ew")

        # Label e entry per host del server
        tk.Label(self.master, text="Server IP:").grid(row=8, column=0, sticky="w")
        entry_client_host = tk.Entry(self.master, textvariable=self.client_host)
        entry_client_host.grid(row=8, column=1, sticky="ew")
        self.client_widgets.append(entry_client_host)

        # Label e entry per porta del server
        tk.Label(self.master, text="Server Port:").grid(row=9, column=0, sticky="w")
        entry_client_port = tk.Entry(self.master, textvariable=self.client_port)
        entry_client_port.grid(row=9, column=1, sticky="ew")
        self.client_widgets.append(entry_client_port)

        # Bottone per connettersi
        self.connect_button = tk.Button(self.master, text="Connect to Server", command=self.connect_to_server)
        self.connect_button.grid(row=11, columnspan=2, sticky="ew")
        self.client_widgets.append(self.connect_button)
        self.stop_client_button = tk.Button(self.master, text="Stop Connection", command=self.stop_connection)
        self.stop_client_button.grid(row=11, columnspan=2, sticky="ew")
        self.client_widgets.append(self.stop_client_button)
        self.stop_client_button.grid_remove()

    def stop_connection(self):
        self.client_connected = False
        if self.client.stop():
            self.update_server_widgets_state(disabled=False)
            self.stop_client_button.grid_remove()
            self.connect_button.grid()
        else:
            self.update_output("Failed to stop client connection")
        # Qui aggiungi la logica per interrompere la connessione

    def connect_to_server(self):
        # Metodo per tentare di connettersi al server
        server_ip = self.client_host.get()
        server_port = self.client_port.get()
        self.update_output(f"Attempting to connect to server at {server_ip}:{server_port}")

        try:
            self.client = run_client(server_ip, server_port, True, self.master, self.update_output)
            self.client_connected = True
            self.update_server_widgets_state(disabled=True)
            self.connect_button.grid_remove()
            self.stop_client_button.grid()
        except Exception as e:
            self.update_output(f"Failed to connect to server: {e}")

    def update_server_widgets_state(self, disabled):
        for widget in self.server_widgets:
            if disabled:
                widget.config(state='disabled')
            else:
                widget.config(state='normal')

    def update_client_widgets_state(self, disabled):
        for widget in self.client_widgets:
            if disabled:
                widget.config(state='disabled')
            else:
                widget.config(state='normal')


# Create the main window and pass it to the GUI
if __name__ == "__main__":

    if _platform.system() == 'Darwin':
        import utils.OSXaccessibilty as OSXaccessibilty
        OSXaccessibilty.check_osx_permissions()
    
    root = tk.Tk()
    app = ServerConfigGUI(root)
    root.mainloop()
