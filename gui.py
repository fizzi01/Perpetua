import signal
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog
from main import run_server
import subprocess


class PositionDialog(simpledialog.Dialog):
    def __init__(self, master, title="", positions=None):
        self.current_positions = positions if positions else {"left": False, "right": False, "up": False,
                                                              "down": False}
        super().__init__(master, title=title)

    def body(self, master):
        self.positions = {"left": tk.BooleanVar(value=self.current_positions.get("left", False)),
                          "right": tk.BooleanVar(value=self.current_positions.get("right", False)),
                          "up": tk.BooleanVar(value=self.current_positions.get("up", False)),
                          "down": tk.BooleanVar(value=self.current_positions.get("down", False))}
        self.checkboxes = {}
        master.grid_columnconfigure(1, weight=1)

        tk.Label(master, text="Host", relief=tk.RAISED).grid(row=1, column=1)

        # Creating checkboxes for each position
        self.checkboxes["left"] = tk.Checkbutton(master, text="Left", var=self.positions["left"])
        self.checkboxes["left"].grid(row=1, column=0)

        self.checkboxes["right"] = tk.Checkbutton(master, text="Right", var=self.positions["right"])
        self.checkboxes["right"].grid(row=1, column=2)

        self.checkboxes["top"] = tk.Checkbutton(master, text="Top", var=self.positions["up"])
        self.checkboxes["top"].grid(row=0, column=1)

        self.checkboxes["bottom"] = tk.Checkbutton(master, text="Bottom", var=self.positions["down"])
        self.checkboxes["bottom"].grid(row=2, column=1)

        return master  # return the widget that should have initial focus

    def apply(self):
        self.result = {pos: var.get() for pos, var in self.positions.items()}


class ServerConfigGUI:
    def __init__(self, master):

        self.server = None
        self.master = master
        self.master.resizable(False, False)
        self.master.title("Server Configuration")

        # Variables for server configuration
        self.host = tk.StringVar(value="0.0.0.0")
        self.port = tk.IntVar(value=5001)
        self.positions = {"left": True, "right": False, "up": False, "down": False}  # default values
        self.logging = tk.BooleanVar(value=True)

        self.process = None

        # Layout configuration
        self.start_button = None
        self.stop_button = None
        self.output = None
        self.create_widgets()

    def create_widgets(self):
        # Label and entry for host
        tk.Label(self.master, text="Host:").grid(row=0, column=0, sticky="w")
        tk.Entry(self.master, textvariable=self.host).grid(row=0, column=1, sticky="ew")

        # Label and entry for port
        tk.Label(self.master, text="Port:").grid(row=1, column=0, sticky="w")
        tk.Entry(self.master, textvariable=self.port).grid(row=1, column=1, sticky="ew")

        tk.Button(self.master, text="Configure Client Positions", command=self.configure_positions).grid(row=2,
                                                                                                         column=1,
                                                                                                         sticky="ew")
        # Checkbox for logging
        tk.Checkbutton(self.master, text="Enable Logging", variable=self.logging).grid(row=3, columnspan=2, sticky="w")

        # Start server button

        # Button to start the server
        self.start_button = tk.Button(self.master, text="Start Server", command=self.start_server)
        self.start_button.grid(row=4, column=0, sticky="ew")

        # Button to stop the server
        self.stop_button = tk.Button(self.master, text="Stop Server", command=self.stop_server)
        self.stop_button.grid(row=4, column=1, sticky="ew")
        self.stop_button.grid_remove()  # Hide the stop button initially

        # ScrolledText for output
        self.output = scrolledtext.ScrolledText(self.master, height=10)
        self.output.grid(row=5, columnspan=2, sticky="nsew")

    def start_server(self):
        # Extract values from GUI
        host = self.host.get()
        port = self.port.get()
        position = self.get_active_positions()
        logging = self.logging.get()

        try:
            # Start the server
            self.server = run_server(host, port, position, logging, 5, 5, self.master, self.update_output)

            # Update button visibility
            self.start_button.grid_remove()
            self.stop_button.grid()
        except Exception as e:
            self.update_output(f"Failed to start server: {e}")

    def update_output(self, message):
        # Insert message to the scrolled text widget
        if self.output.winfo_exists():  # Check if widget still exists
            self.output.insert(tk.END, message + "\n")
            self.output.see(tk.END)

    def configure_positions(self):
        dialog = PositionDialog(self.master, title="Configure Positions", positions=self.positions)
        if dialog.result:
            self.positions = dialog.result  # Update positions based on dialog
            print(self.positions)

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
                self.update_output("Server stopped successfully!\n")
                # When process ends, update the GUI
                self.stop_button.grid_remove()
                self.start_button.grid()


# Create the main window and pass it to the GUI
root = tk.Tk()
app = ServerConfigGUI(root)
root.mainloop()

