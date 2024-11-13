import json
import os

from config.ServerConfig import Clients, Client
from utils import net

from abc import ABC, abstractmethod


class BaseGUIController(ABC):
    def __init__(self, server_config, start_server_event, stop_server_event, exit_event, is_server_running, messager):
        self.server_config = server_config
        self.start_server_event = start_server_event
        self.stop_server_event = stop_server_event
        self.exit_event = exit_event
        self.is_server_running = is_server_running
        self.messager = messager

    @abstractmethod
    def run(self):
        pass

    @abstractmethod
    def configure_server(self):
        pass

    @abstractmethod
    def load_configuration(self, filename):
        pass

    @abstractmethod
    def display_clients_matrix(self):
        pass

    @abstractmethod
    def start_server(self):
        pass

    @abstractmethod
    def stop_server(self):
        pass

    @abstractmethod
    def save_configuration(self, filename):
        pass

    @abstractmethod
    def exit(self):
        pass


class GUIControllerFactory:
    @staticmethod
    def get_controller(gui_type, server_config, start_server_event, stop_server_event, exit_event, is_server_running, messager):
        if gui_type == "terminal":
            return TerminalGUIController(server_config, start_server_event, stop_server_event, exit_event, is_server_running, messager)
        # Aggiungi altre implementazioni di GUI qui
        else:
            raise ValueError(f"Unknown GUI type: {gui_type}")


class TerminalGUIController(BaseGUIController):

    def run(self):
        while True:
            self.messager.print("\n--- Server Configuration Menu ---")
            self.messager.print("1. Configure server manually")
            self.messager.print("2. Load server configuration from file")
            self.messager.print("3. Display clients matrix")
            self.messager.print("4. Start server")
            self.messager.print("5. Stop server")
            self.messager.print("6. Save current configuration to file")
            self.messager.print("7. Exit")
            self.messager.print("\n")
            choice = self.messager.input("Choose an option:")

            if choice == "1":
                self.configure_server()
            elif choice == "2":
                filename = self.messager.input(
                    "Enter configuration file name (default 'server_config.json'): ") or "server_config.json"
                self.load_configuration(filename)
            elif choice == "3":
                self.display_clients_matrix()
            elif choice == "4":
                self.start_server()
            elif choice == "5":
                self.stop_server()
            elif choice == "6":
                filename = self.messager.input(
                    "Enter configuration file name to save (default 'server_config.json'): ") or "server_config.json"
                self.save_configuration(filename)
            elif choice == "7":
                self.exit()
                break
            else:
                self.messager.print("Invalid choice. Please try again.")

    def configure_server(self):
        self.messager.print("Configure your server:")
        server_ip = self.messager.input(f"Enter server IP (default {net.get_local_ip()}): ") or net.get_local_ip()
        server_port = int(self.messager.input("Enter server port (default 5001): ") or 5001)
        wait = int(self.messager.input("Enter wait time for server socket timeout (default 5): ") or 5)
        logging = self.messager.input("Enable logging? (yes/no, default no): ").lower() == "yes"
        screen_threshold = int(self.messager.input("Enter screen threshold (default 10): ") or 10)

        clients_dict = {}
        while True:
            add_client = self.messager.input("Do you want to add a client? (yes/no): ").lower()
            if add_client == "no":
                break
            position = self.messager.input(
                "Enter client position (e.g., 'left', 'right', 'up', 'down', 'up-left', 'up-right', 'down-left', 'down-right'): ")
            address = self.messager.input("Enter client address (IP:PORT): ")
            try:
                ip, port = address.split(":")
                clients_dict[position] = Client(addr=ip, port=port, key_map={})
            except ValueError:
                self.messager.print("Invalid address format. Please try again.")
                continue

        clients = Clients(clients_dict)

        # Set up server configuration
        self.server_config.set_ip(server_ip)
        self.server_config.set_port(server_port)
        self.server_config.set_clients(clients)
        self.server_config.set_wait(wait)
        self.server_config.set_screen_threshold(screen_threshold)
        self.server_config.set_logging(logging)

    def load_configuration(self, filename="server_config.json"):
        """
        Load the server configuration from a JSON file.
        """
        if not os.path.exists(filename):
            print(f"Configuration file {filename} not found.")
            return None

        with open(filename) as config_file:
            config_data = json.load(config_file)

        clients_dict = {}
        for position, address in config_data["clients"].items():
            ip, port = address.split(":")
            clients_dict[position] = Client(addr=ip, key_map={})

        clients = Clients(clients_dict)
        self.server_config.set_ip(config_data["server_ip"])
        self.server_config.set_port(config_data["server_port"])
        self.server_config.set_clients(clients)
        self.server_config.set_wait(config_data["wait"])
        self.server_config.set_screen_threshold(config_data["screen_threshold"])
        self.server_config.set_logging(config_data["logging"])

    def display_clients_matrix(self):
        # Implement display clients matrix logic
        pass

    def start_server(self):
        if not self.is_server_running.is_set():
            self.stop_server_event.clear()
            self.start_server_event.set()
        else:
            self.messager.print("Server is already running.")

    def stop_server(self):
        if self.is_server_running.is_set():
            self.stop_server_event.set()
        else:
            self.messager.print("No server is currently running.")

    def save_configuration(self, filename="server_config.json"):
        """
        Save the server configuration to a JSON file.
        """
        config_data = {
            "server_ip": self.server_config.get_server_ip(),
            "server_port": self.server_config.get_server_port(),
            "clients": {position: f"{client.get_address()}:{client.get_port()}" for position, client in
                        self.server_config.get_clients().clients.items()},
            "wait": self.server_config.get_wait(),
            "screen_threshold": self.server_config.get_screen_threshold(),
            "logging": self.server_config.get_logging()
        }
        with open(filename, 'w') as config_file:
            json.dump(config_data, config_file, indent=4)
        print(f"Configuration saved to {filename} at {os.getcwd()}")

    def exit(self):
        self.stop_server_event.set()
        self.exit_event.set()
        self.messager.print("Exiting ...")
