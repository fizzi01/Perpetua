import shutil
import socket
import ssl
import subprocess
import sys
import os
from threading import Thread
from typing import Optional
import errno

from ServerManager import ServerManager

from config.ServerConfig import Clients, ServerConfig
from gui.GUIController import GUIControllerFactory

from multiprocessing import Process, Event, Pipe
from multiprocessing.managers import BaseManager
from queue import Queue, Empty
from utils import net
from utils.configConstants import *


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


def clear_terminal():
    if 'TERM' not in os.environ:
        os.environ['TERM'] = 'xterm-256color'
    os.system('cls' if os.name == 'nt' else 'clear')


def inputter(input_conn):
    """ get requests to do input calls """
    while True:
        try:
            input_msg = input_conn.recv()  # wait for input request

            if input_msg is None:
                break

            value = input(input_msg)
            clear_terminal()

            # Check if stdin is closed
            if sys.stdin.closed:
                print("Input pipe closed. Exiting.")
                break

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


def check_osx_accessibility():
    import platform as _platform
    if _platform.system() == 'Darwin':
        import utils.OSXaccessibilty as OSXaccessibilty

        print("\033[94mChecking app permissions\033[0m")
        permission = OSXaccessibilty.check_osx_permissions()

        if not permission:
            print(
                "\033[91mA problem occurred while checking app permissions. Please make sure the app has the required permissions.\033[0m")

            from AppKit import NSAlert, NSApp

            alert = NSAlert.alloc().init()
            alert.setMessageText_("A problem occurred while checking app permissions.")
            alert.setInformativeText_("Please make sure the app has the required permissions.")
            alert.addButtonWithTitle_("OK")
            NSApp.activateIgnoringOtherApps_(True)
            alert.runModal()

            return False
        else:
            print("\033[92mApp permissions OK!\033[0m")
            return True
    return True


def process_monitor(exit_event, start_event, controller_proc, threads, pipes, logger):
    # Wait for the stop event to be set
    exit_event.wait()

    # Clean up
    controller_proc.terminate()
    controller_proc.join()

    if not start_event.is_set():
        start_event.set()  # Set the start event to allow the server to stop

    # Force close the input
    sys.stdin.close()
    for pipe in pipes:
        # For each pipe, close the connection
        try:
            pipe.send(None)
        except Exception as e:
            pass
        pipe.close()

    logger.close()

    for thread in threads:
        thread.join()


def create_ssl_client(certfile: str, host: str, port: int):
    context = ssl.create_default_context(cafile=certfile)
    conn = context.wrap_socket(socket.socket(socket.AF_INET), server_hostname=host)
    conn.connect((host, port))
    return conn


def generate_ssl_certificate(certfile: str, keyfile: str):
    if os.name == 'nt':
        return

    # Check if openssl is installed
    if not shutil.which("openssl"):
        print("Please install openssl to generate SSL certificates.")
        return

    if not os.path.exists(certfile) or not os.path.exists(keyfile):
        print("Generating SSL certificate...")
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:4096", "-keyout", keyfile,
            "-out", certfile, "-days", "365", "-nodes", "-subj", "/CN=localhost"
        ])
        print("SSL certificate generated.")
    else:
        print("SSL certificate already exists.")


def initialize_configuration(config_dir: str, certfile, keyfile):
    if not os.path.exists(config_dir):
        os.makedirs(config_dir)
    generate_ssl_certificate(certfile, keyfile)


class MyClassProxy(BaseManager):
    pass


MyClassProxy.register('ServerConfig', ServerConfig)
MyClassProxy.register('ProcessMessage', ProcessMessage)


def main():
    if not check_osx_accessibility():
        return

    config_dir = CONFIG_PATH
    certfile = os.path.join(config_dir, SSL_CERTFILE_NAME)
    keyfile = os.path.join(config_dir, SSL_KEYFILE_NAME)

    initialize_configuration(config_dir, certfile, keyfile)

    manager = MyClassProxy()
    manager.start()

    # Shared server configuration
    server_config = manager.ServerConfig(server_ip=net.get_local_ip(), server_port=5001, clients=Clients(), wait=5,
                                         screen_threshold=10, logging=True)

    stop_server_event = Event()
    start_server_event = Event()
    is_server_running = Event()
    exit_event = Event()

    logger = QueueLogger(Queue())

    # Interprocess messaging
    # Creazione delle pipe per input e output
    p1_input_conn, p2_input_conn = Pipe(duplex=True)
    p1_output_conn, p2_output_conn = Pipe(duplex=True)
    controllerMessager = ProcessMessage(p2_input_conn, p2_output_conn)

    gui_controller = GUIControllerFactory.get_controller("terminal", server_config, start_server_event,
                                                         stop_server_event, exit_event,
                                                         is_server_running, controllerMessager, config_dir)

    controller_process = Process(target=gui_controller.run, daemon=True)

    # Threads for input and output
    output_thread = Thread(target=reader, args=(p1_output_conn,), daemon=True)
    input_thread = Thread(target=inputter, args=(p1_input_conn,), daemon=True)
    server_reader_thread = Thread(target=server_reader, args=(logger,), daemon=True)

    # Starting monitor thread
    monitor_thread = Thread(target=process_monitor, args=(exit_event,
                                                          start_server_event,
                                                          controller_process,
                                                          [input_thread, output_thread, server_reader_thread],
                                                          [p2_input_conn, p2_output_conn, p1_input_conn,
                                                           p1_output_conn],
                                                          logger), daemon=True)

    controller_process.start()
    output_thread.start()
    input_thread.start()
    server_reader_thread.start()
    monitor_thread.start()

    # Check if the server is running and the exit event is not set
    while not exit_event.is_set():

        # Wait for the start event to be set
        start_server_event.wait()
        start_server_event.clear()

        # Recheck if the exit event is set
        if exit_event.is_set():
            break

        server_manager = ServerManager(server_config=server_config, logger=logger,
                                       server_started_event=is_server_running, server_stop_event=stop_server_event)
        server_monitor_thread = Thread(target=server_manager.monitor_server, daemon=True)
        server_monitor_thread.start()

        server_manager.start_server()

        server_monitor_thread.join()

    monitor_thread.join()

    # Clean up last resources
    manager.shutdown()

    return 0


if __name__ == "__main__":
    main()
