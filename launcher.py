import os
import signal
from multiprocessing import Pipe, Process, Event, managers
from queue import Queue
from threading import Thread
from typing import Optional

from app.ProcessMonitor import ProcessMonitor
from app.io.IOManager import IOManager
from config.ServerConfig import ServerConfig, Clients
from config.ClientConfig import ClientConfig
from app.managers.ServerManager import ServerManager
from app.managers.ClientManager import ClientManager
from app.gui.GUIController import GUIControllerFactory, BaseGUIController

from app.io.common import ProcessMessage, QueueLogger

from utils.net import NetUtils
from utils.misc.OSXaccessibilty import check_osx_permissions


def clear_terminal(msg=None):
    if 'TERM' not in os.environ:
        os.environ['TERM'] = 'xterm-256color'
    os.system('cls' if os.name == 'nt' else 'clear')


class ApplicationLauncher:
    """
    Classe principale di avvio dell'applicazione (server + client + GUI),
    che coordina la configurazione, i processi e i thread.
    """

    def __init__(self):
        # Eventi di controllo
        self.exit_event = Event()

        # Eventi per il server
        self.stop_server_event = Event()
        self.start_server_event = Event()
        self.is_server_running = Event()

        # Eventi per il client
        self.stop_client_event = Event()
        self.start_client_event = Event()
        self.is_client_running = Event()

        # Logging
        self.logger_queue = Queue()
        self.logger = QueueLogger(self.logger_queue)

        # Pipe per input/output (GUI)
        self.p1_input_conn, self.p2_input_conn = Pipe(duplex=True)
        self.p1_output_conn, self.p2_output_conn = Pipe(duplex=True)

        self.gui_controller: Optional[BaseGUIController] = None
        self.gui_process: Optional[Process] = None

        # Manager (multiprocessing)
        self.manager = managers.BaseManager()

        # Configurazioni
        self.server_config = None
        self.client_config = None

        # Manager di server/client
        self.server_manager = None
        self.client_manager = None

        # Oggetti IOManager
        self.input_manager: Optional[IOManager] = None
        self.output_manager: Optional[IOManager] = None
        self.logger_manager: Optional[IOManager] = None

        # Thread di monitor
        self.process_monitor: Optional[ProcessMonitor] = None

        self._thread_pool = []
        self.server_reader_thread: Optional[Thread] = None

    def _handle_sigterm(self, signum, frame):
        self.exit_event.set()
        self.stop_server_event.set()
        self.stop_client_event.set()

    @staticmethod
    def check_osx_accessibility():
        import platform
        if platform.system() == 'Darwin':
            if not check_osx_permissions():
                print("macOS permissions not granted!")
                return False
        return True

    def init_configs(self):
        # Avvia manager
        self.manager.register('ServerConfig', ServerConfig)
        self.manager.register('ClientConfig', ClientConfig)
        self.manager.register('ProcessMessage', ProcessMessage)
        self.manager.start()

        # Crea configurazioni
        self.server_config = self.manager.ServerConfig(
            server_ip=NetUtils.get_local_ip(),
            server_port=5001,
            clients=Clients(),
            wait=5,
            screen_threshold=10,
            logging=True
        )

        self.client_config = self.manager.ClientConfig(
            server_ip="",
            server_port=5001,
            use_ssl=True,
            certfile=None,
            logging=True
        )

    def init_controller(self):
        # Crea l'oggetto ProcessMessage con la pipe
        controller_messager = ProcessMessage(self.p2_input_conn, self.p2_output_conn)

        # Crea GUI
        self.gui_controller = GUIControllerFactory.get_controller(
            "terminal",
            server_config=self.server_config,
            client_config=self.client_config,
            start_server_event=self.start_server_event,
            stop_server_event=self.stop_server_event,
            exit_event=self.exit_event,
            is_server_running=self.is_server_running,
            messager=controller_messager,
            stop_client_event=self.stop_client_event,
            start_client_event=self.start_client_event,
            is_client_running=self.is_client_running
        )

        # Process per eseguire la GUI
        self.gui_process = Process(target=self.gui_controller.run, daemon=True)

    def init_signal_handlers(self):
        signal.signal(signal.SIGTERM, self._handle_sigterm)

    def start_processes_and_threads(self):
        # Avvia process della GUI
        self.gui_process.start()

        ############################
        # IOManager
        ############################

        # -> OutputManager: legge dalla pipe p1_output_conn e stampa su console
        self.output_manager = IOManager(
            name="OutputManager",
            pipe=self.p1_output_conn,
            mode=IOManager.READER_MODE,
            output_stream=print,
            input_stream=input,
            on_message_callback=clear_terminal
        )
        self._thread_pool.append(self.output_manager)

        # -> InputManager: legge i prompt dalla pipe p1_input_conn e chiede input all'utente
        self.input_manager = IOManager(
            name="InputManager",
            pipe=self.p1_input_conn,
            mode=IOManager.INPUT_MODE,
            input_stream=input,
            output_stream=print,
            auto_start=True
        )
        self._thread_pool.append(self.input_manager)

        # -> LoggerManager: legge dalla coda logger_queue e stampa su console
        self.logger_manager = IOManager(
            name="LoggerManager",
            queue=self.logger_queue,
            mode=IOManager.READER_MODE,
            output_stream=print,
            input_stream=input,
        )
        self._thread_pool.append(self.logger_manager)

        # Start threads
        for t in self._thread_pool:
            t.start()

        # Avvia il monitor thread
        self._start_process_monitor()

    def _start_process_monitor(self):
        pipes = [self.p2_input_conn, self.p2_output_conn]
        self.process_monitor = ProcessMonitor(
            exit_event=self.exit_event,
            start_event=self.start_server_event,  # o un event generico
            processes=self.gui_process,
            threads=self._thread_pool,
            pipes=pipes,
            on_stop_callback=self._on_monitor_stopped
        )
        self.process_monitor.start()

    def _on_monitor_stopped(self):
        print("Monitor thread stopped. Doing final cleanup...")
        try:
            self.manager.shutdown()
        except Exception as e:
            print(f"Error shutting down manager: {e}")

    def loop_run(self):
        while not self.exit_event.is_set():
            # Attende che l’utente scelga di avviare client o server
            while not (self.start_client_event.is_set() or self.start_server_event.is_set()):
                if self.exit_event.wait(timeout=0.1):
                    break
            if self.exit_event.is_set():
                break

            # Avvio server
            if self.start_server_event.is_set():
                self.start_server_event.clear()
                if self.exit_event.is_set():
                    break

                self.server_manager = ServerManager(
                    server_config=self.server_config,
                    logger=self.logger,
                    server_started_event=self.is_server_running,
                    server_stop_event=self.stop_server_event
                )
                server_monitor_thread = Thread(target=self.server_manager.monitor_server, daemon=True)
                server_monitor_thread.start()

                self.server_manager.start_server()
                server_monitor_thread.join()

            # Avvio client
            if self.start_client_event.is_set():
                self.start_client_event.clear()
                if self.exit_event.is_set():
                    break

                self.client_manager = ClientManager(
                    client_config=self.client_config,
                    logger=self.logger,
                    client_started_event=self.is_client_running,
                    client_stop_event=self.stop_client_event
                )
                client_monitor_thread = Thread(target=self.client_manager.monitor_client, daemon=True)
                client_monitor_thread.start()

                self.client_manager.start_client()
                client_monitor_thread.join()

    def run(self) -> int:
        # 1) Check macOS perms (opzionale)
        if not self.check_osx_accessibility():
            return 1

        # 2) Setup signal
        self.init_signal_handlers()

        # 3) Inizializza manager e config
        self.init_configs()

        # 4) Crea la GUI controller e process
        self.init_controller()

        # 5) Avvia i thread e processi di I/O
        self.start_processes_and_threads()

        # 6) Loop principale
        self.loop_run()

        # 7) Attendiamo la fine del process monitor
        if self.process_monitor:
            self.process_monitor.join()

        return 0

