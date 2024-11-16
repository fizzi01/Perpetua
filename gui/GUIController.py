import json
import os

from tabulate import tabulate

from config.ServerConfig import Clients, Client, ServerConfig
from utils import net

from abc import ABC, abstractmethod

from utils.configConstants import *
from utils.metadataExtractor import extract_metadata


class BaseGUIController(ABC):

    def __init__(self, server_config, start_server_event, stop_server_event, exit_event, is_server_running, messager, folder_path):

        self.server_config = server_config
        self.server_config_metadata = extract_metadata(ServerConfig)

        self.start_server_event = start_server_event
        self.stop_server_event = stop_server_event
        self.exit_event = exit_event
        self.is_server_running = is_server_running
        self.messager = messager
        self.folder_path = folder_path

    @abstractmethod
    def run(self):
        pass

    @abstractmethod
    def configure_server(self):
        pass

    @abstractmethod
    def edit_configuration(self):
        pass

    def load_configuration(self):
        """
        Load the server configuration from a JSON file.
        """
        filename = os.path.join(self.folder_path, CONFIG_FILE)
        if not os.path.exists(filename):
            self.messager.print(f"Configuration file {filename} not found.")
            if not os.path.exists(self.folder_path):
                self.messager.print(f"Creating folder {self.folder_path}")
                os.makedirs(self.folder_path, exist_ok=True)
            return

        with open(filename) as config_file:
            config_data = json.load(config_file)

        metadata = self.server_config_metadata
        for param_name, methods in metadata["set_methods"].items():
            try:
                set_method = getattr(self.server_config, param_name)
                value = config_data.get(param_name[4:], None)  # Remove 'set_' prefix
                if value is not None:
                    set_method(value)
            except Exception as e:
                self.messager.print(f"Error setting {param_name}: {e}")

        clients_dict = {}
        for position, address in config_data.get("clients", {}).items():
            try:
                clients_dict[position] = Client(addr=address, key_map={})
            except ValueError as e:
                self.messager.print(f"Error parsing client address {address}: {e}")

        clients = Clients(clients_dict)
        self.server_config.set_clients(clients)

    @abstractmethod
    def display_clients_matrix(self):
        pass

    @abstractmethod
    def start_server(self):
        if not self.is_server_running.is_set():
            self.stop_server_event.clear()
            self.start_server_event.set()
        else:
            self.messager.print("Server is already running.")

    @abstractmethod
    def stop_server(self):
        if self.is_server_running.is_set():
            self.is_server_running.clear()  # Clear the server running status
            self.stop_server_event.set()

            # Wait for the server to stop
            self.is_server_running.wait()
        else:
            self.messager.print("No server is currently running.")

    @abstractmethod
    def save_configuration(self):
        """
        Save the server configuration to a JSON file using metadata.
        """
        path = os.path.join(self.folder_path, CONFIG_FILE)
        metadata = self.server_config_metadata
        config_data = {}

        for param_name, methods in metadata["get_methods"].items():
            try:
                get_method = getattr(self.server_config, param_name)
                value = get_method()
                config_data[param_name[4:]] = value  # Remove 'get_' prefix
            except Exception as e:
                self.messager.print(f"Error getting {param_name}: {e}")

        # Save clients separately
        config_data["clients"] = {position: f"{client.get_address()}" for position, client in
                                  self.server_config.get_clients().clients.items()}

        with open(path, 'w') as config_file:
            json.dump(config_data, config_file, indent=4)

    @abstractmethod
    def exit(self):
        self.stop_server()
        self.exit_event.set()
        self.messager.print("Exiting ...")


class GUIControllerFactory:
    @staticmethod
    def get_controller(gui_type, server_config, start_server_event, stop_server_event, exit_event, is_server_running, messager, folder_path):
        if gui_type == "terminal":
            return TerminalGUIController(server_config, start_server_event, stop_server_event, exit_event, is_server_running, messager, folder_path)
        # Aggiungi altre implementazioni di GUI qui
        else:
            raise ValueError(f"Unknown GUI type: {gui_type}")


class TerminalGUIController(BaseGUIController):

    def run(self):
        # Startup load configuration
        self.load_configuration()
        while True:
            self.messager.print("\n--- Server Configuration Menu ---")
            self.messager.print("1. Configure server manually")
            self.messager.print("2. Display clients matrix")
            self.messager.print("3. Start server")
            self.messager.print("4. Stop server")
            self.messager.print("5. Edit configuration")
            self.messager.print("6. Exit")
            self.messager.print("\n")
            choice = self.messager.input("Choose an option:")

            if choice == "1":
                self.configure_server()
            elif choice == "2":
                self.display_clients_matrix()
            elif choice == "3":
                self.start_server()
            elif choice == "4":
                self.stop_server()
            elif choice == "5":
                self.edit_configuration()
            elif choice == "6":
                self.exit()
                break
            else:
                self.messager.print("Invalid choice. Please try again.")

    def edit_configuration(self):
        while True:
            self.messager.print("Current configuration:")
            metadata = self.server_config_metadata

            # Display current configuration
            for param_name, methods in metadata["set_methods"].items():
                if param_name == "set_clients":
                    continue  # Skip client-related methods initially

                display_name = methods["display_name"]
                get_method_name = f"get_{param_name[4:]}"
                get_method = getattr(self.server_config, get_method_name, None)
                if callable(get_method):
                    current_value = get_method()
                    self.messager.print(f"{display_name} ({param_name[4:]}): {current_value}")

            # Ask user which parameter to modify
            param_to_modify = self.messager.input("Enter the parameter name to modify (or 'exit' to finish): ")
            if param_to_modify.lower() == 'exit':
                break

            # Find the corresponding set method
            set_method_name = f"set_{param_to_modify}"
            set_method = getattr(self.server_config, set_method_name, None)
            if callable(set_method):
                current_value = getattr(self.server_config, f"get_{param_to_modify}")()
                new_value = self.messager.input(
                    f"Enter new value for {param_to_modify} (current value: {current_value}): ")

                # Convert input to the correct type
                param_type = metadata["set_methods"][set_method_name]["parameters"][0]["type"]
                if param_type.find("int") != -1:
                    new_value = int(new_value)
                elif param_type.find("bool") != -1:
                    new_value = new_value.lower() in ['true', 'yes', '1']

                # Set the new value
                set_method(new_value)

                # Save the configuration automatically
                self.save_configuration()
            else:
                self.messager.print(f"Invalid parameter name: {param_to_modify}. Please try again.")

    def configure_server(self):
        self.messager.print("Configure your server:")
        metadata = self.server_config_metadata

        for param_name, methods in metadata["set_methods"].items():
            if param_name == "set_clients":
                continue  # Skip client-related methods initially

            display_name = methods["display_name"]
            get_method_name = f"get_{param_name[4:]}"
            get_method = getattr(self.server_config, get_method_name, None)
            if callable(get_method):
                current_value = get_method()
                input_value = self.messager.input(
                    f"Set {display_name} (default {current_value}): ") or current_value

                # Convert input to the correct type
                param_type = methods["parameters"][0]["type"]
                if param_type.find("int") != -1:
                    input_value = int(input_value)
                elif param_type.find("bool") != -1:
                    input_value = input_value.lower() in ['true', 'yes', '1']

                # Set the value using the set method
                set_method = getattr(self.server_config, param_name)
                set_method(input_value)

        # Configure clients
        clients_dict = {}
        while True:
            add_client = self.messager.input("Do you want to add a client? (yes/no): ").lower()
            if add_client == "no":
                break
            position = self.messager.input("Enter client position (e.g., 'left', 'right', 'up', 'down'): ")
            address = self.messager.input("Enter client address (IP): ")
            try:
                clients_dict[position] = Client(addr=address, key_map={})
            except ValueError:
                self.messager.print("Invalid address format. Please try again.")
                continue

        clients = Clients(clients_dict)
        self.server_config.set_clients(clients)

        self.save_configuration()
        self.load_configuration()

    def save_configuration(self):
        super().save_configuration()

    def display_clients_matrix(self):
        # Dynamic matrix display
        clients = self.server_config.get_clients().clients
        matrix = [["" for _ in range(3)] for _ in range(3)]

        # Add server at the center
        server_address = "Host"
        matrix[1][1] = server_address

        for position, client in clients.items():
            address = f"{client.get_address()}"
            if position == "left":
                matrix[1][0] = address
            elif position == "right":
                matrix[1][2] = address
            elif position == "up":
                matrix[0][1] = address
            elif position == "down":
                matrix[2][1] = address

        # Print the matrix in a formatted way using tabulate
        print(tabulate(matrix, tablefmt="grid",colalign=("center", "center", "center"),headers=["Left", "", "Right"], showindex=["Up", "Host", "Down"]))

    def start_server(self):
        super().start_server()

    def stop_server(self):
        super().stop_server()

    def exit(self):
        super().exit()
