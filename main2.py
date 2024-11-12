import threading
from threading import Thread
from typing import Optional
import errno

from config.ServerConfig import Clients, Client, ServerConfig
from server import Server2 as Server
from multiprocessing import Process, Event, Pipe
from multiprocessing.managers import BaseManager
from queue import Queue, Empty
import json
import os
from utils import net


class QueueLogger:
    def __init__(self, queue: Optional[Queue] = None):
        self.queue = queue
        self.closed = False

    def write(self, msg):
        self.queue.put(msg)

    def flush(self):
        pass

    def read(self, timeout=1):
        return self.queue.get(timeout=timeout)

    def close(self):
        self.queue.put(None)
        self.closed = True


class ProcessMessage:
    def __init__(self, input_conn: Pipe, output_conn: Pipe):
        self.input_conn = input_conn
        self.output_conn = output_conn

    def input(self, msg):
        self.input_conn.send(msg)
        return self.input_conn.recv()

    def print(self, msg):
        self.output_conn.send(msg)

    def read(self):
        return self.output_conn.recv()


def inputter(input_conn):
    """ get requests to do input calls """
    while True:
        try:
            input_msg = input_conn.recv()  # wait for input request
            if input_msg is None:
                break
            value = input(input_msg)
            input_conn.send(value)  # send inputted value
        except Exception as e:
            if e.args[0] == errno.EBADF:
                break
            print(f"Error reading from input pipe: {e}")
            break


def reader(output_conn):
    """ get requests to do output calls """
    while True:
        try:
            output_msg = output_conn.recv()  # wait for output request
            if output_msg is None:
                break
            print(output_msg)
        except Exception as e:
            # Check if e is errno.EBADF
            if e.args[0] == errno.EBADF:
                break
            print(f"Error reading from output pipe: {e}")
            break


def server_reader(queue):
    while True:
        try:
            if queue.closed:
                break
            msg = queue.read(timeout=1)
            if msg is None and queue.closed:
                break
            print(msg)
        # Exception empty queue
        except Empty:
            if queue.closed:
                print("No messages to read. Closing logger.")
                break
        except Exception as e:
            if queue.closed:
                print(f"Error reading from queue: {e}")
                break
            continue


def display_clients_matrix(clients):
    """
    Display an interactive matrix showing the server at the center and clients around it.
    """
    positions = ["up", "up-left", "up-right", "left", "center", "right", "down-left", "down", "down-right"]
    matrix = [
        [" ", "Client (up)", " "],
        ["Client (up-left)", "server", "Client (up-right)"],
        ["Client (left)", "server", "Client (right)"],
        ["Client (down-left)", "Client (down)", "Client (down-right)"]
    ]

    client_positions = clients.get_possible_positions()

    # Update matrix based on client positions
    for position in client_positions:
        if position in positions:
            if position == "up":
                matrix[0][1] = "Client (up)"
            elif position == "up-left":
                matrix[1][0] = "Client (up-left)"
            elif position == "up-right":
                matrix[1][2] = "Client (up-right)"
            elif position == "left":
                matrix[2][0] = "Client (left)"
            elif position == "right":
                matrix[2][2] = "Client (right)"
            elif position == "down-left":
                matrix[3][0] = "Client (down-left)"
            elif position == "down":
                matrix[3][1] = "Client (down)"
            elif position == "down-right":
                matrix[3][2] = "Client (down-right)"

    # Display the matrix
    print("Current Network Layout:")
    for row in matrix:
        print(" | ".join(row))
    print("\n")


def save_configuration(server_config, filename="server_config.json"):
    """
    Save the server configuration to a JSON file.
    """
    config_data = {
        "server_ip": server_config.get_server_ip(),
        "server_port": server_config.get_server_port(),
        "clients": {position: f"{client.get_address()}:{client.get_port()}" for position, client in
                    server_config.get_clients().clients.items()},
        "wait": server_config.get_wait(),
        "screen_threshold": server_config.get_screen_threshold(),
        "logging": server_config.get_logging()
    }
    with open(filename, 'w') as config_file:
        json.dump(config_data, config_file, indent=4)
    print(f"Configuration saved to {filename} at {os.getcwd()}")


def load_configuration(filename="server_config.json", server_config=None):
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
    server_config.set_ip(config_data["server_ip"])
    server_config.set_port(config_data["server_port"])
    server_config.set_clients(clients)
    server_config.set_wait(config_data["wait"])
    server_config.set_screen_threshold(config_data["screen_threshold"])
    server_config.set_logging(config_data["logging"])

    print(f"Configuration loaded from {filename} at {os.getcwd()}")
    print(f"Server IP: {server_config.get_server_ip()}")
    print(f"Server Port: {server_config.get_server_port()}")
    print(f"Wait: {server_config.get_wait()}")
    print(f"Screen Threshold: {server_config.get_screen_threshold()}")
    print(f"Logging: {server_config.get_logging()}")
    print("Clients:")
    for position, client in server_config.get_clients().clients.items():
        print(f"\tPosition: {position}\n\tAddress: {client.get_address()}\n\tPort: {client.get_port()}\n\n")

    return server_config


def check_osx_accessibility():
    import platform as _platform
    if _platform.system() == 'Darwin':
        import utils.OSXaccessibilty as OSXaccessibilty

        permission = OSXaccessibilty.check_osx_permissions()

        if not permission:
            print(
                "\033[91mPlease enable the accessibility permission in System Preferences -> Security & Privacy -> Monitoring Input\033[0m")
            return False
        else:
            print("\033[92mAccessibility permission granted\033[0m")
            return True
    return True


def process_monitor(stop_event, server, controller_proc, threads, pipes, logger):
    # Check if server is running
    stop_event.wait()
    if not server.stop():
        print("Failed to stop server.")
        stop_event.clear()

    # Clean up
    controller_proc.terminate()
    controller_proc.join()

    for pipe in pipes:
        pipe.send(None)
        pipe.close()

    logger.close()

    for thread in threads:
        thread.join()


def controller(server_config, start_event, stop_event, is_server_running, messager: ProcessMessage):
    while True:
        messager.print("\n--- Server Configuration Menu ---")
        messager.print("1. Configure server manually")
        messager.print("2. Load server configuration from file")
        messager.print("3. Display clients matrix")
        messager.print("4. Start server")
        messager.print("5. Stop server")
        messager.print("6. Save current configuration to file")
        messager.print("7. Exit")
        messager.print("\n")
        choice = messager.input("Choose an option:")

        if choice == "1":
            messager.print("Configure your server:")
            server_ip = messager.input(f"Enter server IP (default {net.get_local_ip()}): ") or net.get_local_ip()
            server_port = int(messager.input("Enter server port (default 5001): ") or 5001)
            wait = int(messager.input("Enter wait time for server socket timeout (default 5): ") or 5)
            logging = messager.input("Enable logging? (yes/no, default no): ").lower() == "yes"
            screen_threshold = int(messager.input("Enter screen threshold (default 10): ") or 10)

            clients_dict = {}
            while True:
                add_client = messager.input("Do you want to add a client? (yes/no): ").lower()
                if add_client == "no":
                    break
                position = messager.input(
                    "Enter client position (e.g., 'left', 'right', 'up', 'down', 'up-left', 'up-right', 'down-left', 'down-right'): ")
                address = messager.input("Enter client address (IP:PORT): ")
                try:
                    ip, port = address.split(":")
                    clients_dict[position] = Client(addr=ip, port=port, key_map={})
                except ValueError:
                    messager.print("Invalid address format. Please try again.")
                    continue

            clients = Clients(clients_dict)

            # Set up server configuration
            server_config.set_ip(server_ip)
            server_config.set_port(server_port)
            server_config.set_clients(clients)
            server_config.set_wait(wait)
            server_config.set_screen_threshold(screen_threshold)
            server_config.set_logging(logging)

        elif choice == "2":
            filename = messager.input(
                "Enter configuration file name (default 'server_config.json'): ") or "server_config.json"
            server_config = load_configuration(filename, server_config)

        elif choice == "3":
            if server_config:
                display_clients_matrix(server_config.get_clients())
            else:
                messager.print("No server configuration available. Please configure the server first.")

        elif choice == "4":
            if server_config:
                if not is_server_running.is_set():
                    stop_event.clear()
                    start_event.set()
                else:
                    messager.print("Server is already running.")
            else:
                messager.print("No server configuration available. Please configure the server first.")

        elif choice == "5":
            if is_server_running.is_set():
                stop_event.set()
                break
            else:
                messager.print("No server is currently running.")

        elif choice == "6":
            if server_config:
                filename = messager.input(
                    "Enter configuration file name to save (default 'server_config.json'): ") or "server_config.json"
                save_configuration(server_config, filename)
            else:
                messager.print("No server configuration available to save.")

        elif choice == "7":
            if is_server_running:
                stop_event.set()
            messager.print("Exiting.")
            break

        else:
            messager.print("Invalid choice. Please try again.")


class MyClassProxy(BaseManager):
    pass


MyClassProxy.register('ServerConfig', ServerConfig)
MyClassProxy.register('ProcessMessage', ProcessMessage)


def main():
    if not check_osx_accessibility():
        return

    manager = MyClassProxy()
    manager.start()

    server_config = manager.ServerConfig(server_ip=net.get_local_ip(), server_port=5001, clients=Clients(), wait=5,
                                         screen_threshold=10, logging=True)
    stop_event = Event()
    start_event = Event()
    is_server_running = Event()

    logger = QueueLogger(Queue())

    # Interprocess messaging
    # Creazione delle pipe per input e output
    p1_input_conn, p2_input_conn = Pipe(duplex=True)
    p1_output_conn, p2_output_conn = Pipe(duplex=True)

    controllerMessager = ProcessMessage(p2_input_conn, p2_output_conn)

    # Avvio del thread per gestire l'output
    output_thread = Thread(target=reader, args=(p1_output_conn,), daemon=True)
    output_thread.start()

    # Avvio del thread per gestire l'input
    input_thread = Thread(target=inputter, args=(p1_input_conn,), daemon=True)
    input_thread.start()

    controller_process = Process(target=controller,
                                 args=(server_config, start_event, stop_event, is_server_running, controllerMessager),
                                 daemon=True)
    controller_process.start()

    # Wait for the start event to be set
    start_event.wait()
    server = Server.Server(
        host=server_config.get_server_ip(),
        port=server_config.get_server_port(),
        clients=server_config.get_clients(),
        wait=server_config.get_wait(),
        logging=server_config.get_logging(),
        screen_threshold=server_config.get_screen_threshold(),
        stdout=logger.write
    )

    # Server reader
    server_reader_thread = Thread(target=server_reader, args=(logger,), daemon=True)
    server_reader_thread.start()

    # Starting monitor thread
    monitor_thread = Thread(target=process_monitor, args=(stop_event, server,
                                                          controller_process,
                                                          [input_thread, output_thread, server_reader_thread],
                                                          [p1_input_conn, p1_output_conn],
                                                          logger), daemon=True)
    monitor_thread.start()

    # Starting server
    server.start()

    is_server_running.set()
    monitor_thread.join()

    # Clean up last resources
    manager.shutdown()

    return 0


if __name__ == "__main__":
    main()
