from tabulate import tabulate

from config.ClientConfig import ClientConfig
from config.ServerConfig import Clients, Client, ServerConfig
from config.AppConfigHandler import AppConfigHandler
from config.configConstants import *

from abc import ABC, abstractmethod

from utils.metadataExtractor import extract_metadata

import qrcode_terminal

from utils import net


class BaseGUIController(ABC):

    def __init__(self, server_config, client_config, start_server_event, stop_server_event, exit_event,
                 is_server_running, messager, stop_client_event, start_client_event, is_client_running):

        self.server_config = server_config
        self.client_config = client_config
        self.server_config_metadata = extract_metadata(ServerConfig)
        self.client_config_metadata = extract_metadata(ClientConfig)

        self.start_server_event = start_server_event
        self.stop_server_event = stop_server_event
        self.stop_client_event = stop_client_event
        self.start_client_event = start_client_event
        self.exit_event = exit_event
        self.is_server_running = is_server_running
        self.is_client_running = is_client_running
        self.messager = messager

        # Inizializza AppConfigHandler
        self.config_handler = AppConfigHandler()

    @abstractmethod
    def run(self):
        pass

    @abstractmethod
    def configure_server(self):
        pass

    @abstractmethod
    def edit_server_configuration(self):
        pass

    def load_server_configuration(self):
        """
        Load the server configuration from a JSON file.
        """
        config_data = self.config_handler.load_config(SERVER_KEY)
        if not config_data:
            self.messager.print(f"Server configuration file not found.")
            return

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

    def load_client_configuration(self):
        """
        Load the client configuration from a JSON file.
        """
        config_data = self.config_handler.load_config(CLIENT_KEY)
        if not config_data:
            self.messager.print(f"Client configuration file not found.")
            return

        metadata = self.client_config_metadata
        for param_name, methods in metadata["set_methods"].items():
            try:
                set_method = getattr(self.client_config, param_name)
                value = config_data.get(param_name[4:], None)  # Remove 'set_' prefix
                if value is not None:
                    set_method(value)
            except Exception as e:
                self.messager.print(f"Error setting {param_name}: {e}")

    @abstractmethod
    def display_clients_matrix(self):
        pass

    @abstractmethod
    def start_server(self):
        if not self.is_server_running.is_set() and not self.is_client_running.is_set():
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
    def start_client(self):
        if not self.is_client_running.is_set() and not self.is_server_running.is_set():
            self.stop_client_event.clear()
            self.start_client_event.set()
        else:
            self.messager.print("Client is already running.")

    @abstractmethod
    def stop_client(self):
        if self.is_client_running.is_set():
            self.is_client_running.clear()  # Clear the client running status
            self.stop_client_event.set()

            # Wait for the client to stop
            self.is_client_running.wait()
        else:
            self.messager.print("No client is currently running.")

    @abstractmethod
    def save_server_configuration(self):
        """
        Save the server configuration to a JSON file using metadata.
        """
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

        self.config_handler.save_config(SERVER_KEY, config_data)

    def save_client_configuration(self):
        """
        Save the client configuration to a JSON file using metadata.
        """
        metadata = self.client_config_metadata
        config_data = {}

        for param_name, methods in metadata["get_methods"].items():
            try:
                get_method = getattr(self.client_config, param_name)
                value = get_method()
                config_data[param_name[4:]] = value  # Remove 'get_' prefix
            except Exception as e:
                self.messager.print(f"Error getting {param_name}: {e}")

        self.config_handler.save_config(CLIENT_KEY, config_data)

    def load_server_certificate(self):
        """
        Load the server certificate from a file.
        """
        server_ip = self.server_config.get_server_ip()
        force = False
        if server_ip != net.get_local_ip():
            server_ip = net.get_local_ip()
            force = True

        certfile, keyfile = self.config_handler.load_ssl_certificate(server_ip=server_ip,force=force)

        if certfile and keyfile:
            self.server_config.set_certfile(certfile)
            self.server_config.set_keyfile(keyfile)
            self.messager.print("Server certificates loaded successfully.")

    def ensure_client_certfile(self):
        certfile = self.client_config.get_server_certfile()
        if not certfile:
            certfile_path = self.messager.input("Enter the path to the server certificate file: ")
            self.client_config.set_server_certfile(certfile_path)
            self.save_client_configuration()

    @abstractmethod
    def exit(self):
        self.stop_server()
        self.stop_client()
        self.exit_event.set()
        self.messager.print("Exiting ...")


class GUIControllerFactory:
    @staticmethod
    def get_controller(gui_type, server_config, client_config, start_server_event, stop_server_event, exit_event,
                       is_server_running, messager, stop_client_event=None, start_client_event=None,
                       is_client_running=None):
        if gui_type == "terminal":
            return TerminalGUIController(server_config, client_config, start_server_event, stop_server_event,
                                         exit_event, is_server_running, messager, stop_client_event,
                                         start_client_event, is_client_running)
        # Aggiungi altre implementazioni di GUI qui
        else:
            raise ValueError(f"Unknown GUI type: {gui_type}")


class TerminalGUIController(BaseGUIController):

    def run(self):
        # Startup load configuration
        self.load_server_configuration()
        self.load_client_configuration()
        while True:
            self.messager.print("\n--- Server and Client Configuration Menu ---")
            self.messager.print("1. Configure server manually")
            self.messager.print("2. Display clients matrix")
            self.messager.print("3. Start server")
            self.messager.print("4. Stop server")
            self.messager.print("5. Start client")
            self.messager.print("6. Stop client")
            self.messager.print("7. Edit server configuration")
            self.messager.print("8. Edit client configuration")
            self.messager.print("9. Generate server certificate QR code")
            self.messager.print("10. Exit")
            self.messager.print("\n")
            choice = self.messager.input("Choose an option:")

            if choice == "1":
                self.configure_server()
            elif choice == "2":
                self.display_clients_matrix()
            elif choice == "3":
                self.load_server_certificate()
                self.start_server()
            elif choice == "4":
                self.stop_server()
            elif choice == "5":
                self.ensure_client_certfile()
                self.start_client()
            elif choice == "6":
                self.stop_client()
            elif choice == "7":
                self.edit_server_configuration()
            elif choice == "8":
                self.edit_client_configuration()
            elif choice == "9":
                self.load_server_certificate()
                self.generate_server_cert_qr_code()
            elif choice == "10":
                self.exit()
                break
            else:
                self.messager.print("Invalid choice. Please try again.")

    def generate_server_cert_qr_code(self):
        certfile = self.server_config.get_certfile()
        if not certfile:
            self.messager.print("Server certificate file not found.")
            return

        with open(certfile, 'r') as file:
            cert_data = file.read()

        qr = qrcode_terminal.qr_terminal_str(cert_data)

        self.messager.print(qr)
        self.messager.print("Server certificate QR code displayed in terminal.")

    def edit_server_configuration(self):
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
                self.save_server_configuration()
            else:
                self.messager.print(f"Invalid parameter name: {param_to_modify}. Please try again.")

    def edit_client_configuration(self):
        while True:
            self.messager.print("Current client configuration:")
            metadata = self.client_config_metadata

            # Display current configuration
            for param_name, methods in metadata["set_methods"].items():
                display_name = methods["display_name"]
                get_method_name = f"get_{param_name[4:]}"
                get_method = getattr(self.client_config, get_method_name, None)
                if callable(get_method):
                    current_value = get_method()
                    self.messager.print(f"{display_name} ({param_name[4:]}): {current_value}")

            # Ask user which parameter to modify
            param_to_modify = self.messager.input("Enter the parameter name to modify (or 'exit' to finish): ")
            if param_to_modify.lower() == 'exit':
                break

            # Find the corresponding set method
            set_method_name = f"set_{param_to_modify}"
            set_method = getattr(self.client_config, set_method_name, None)
            if callable(set_method):
                current_value = getattr(self.client_config, f"get_{param_to_modify}")()
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
                self.save_client_configuration()
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
                    input_value = str(input_value).lower() in ['true', 'yes', '1']

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
            if position not in ["left", "right", "up", "down"]:
                self.messager.print("Invalid position. Please try again.")
                continue
            address = self.messager.input("Enter client address (IP): ")
            try:
                clients_dict[position] = Client(addr=address, key_map={})
            except ValueError:
                self.messager.print("Invalid address format. Please try again.")
                continue

        clients = Clients(clients_dict)
        self.server_config.set_clients(clients)

        self.save_server_configuration()
        self.load_server_configuration()

    def save_server_configuration(self):
        super().save_server_configuration()

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
        print(tabulate(matrix, tablefmt="grid", colalign=("center", "center", "center"), headers=["Left", "", "Right"],
                       showindex=["Up", "Host", "Down"]))

    def start_server(self):
        super().start_server()

    def stop_server(self):
        super().stop_server()

    def start_client(self):
        super().start_client()

    def stop_client(self):
        super().stop_client()

    def exit(self):
        super().exit()
