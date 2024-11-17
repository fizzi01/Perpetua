from client import Client
from config.ClientConfig import ClientConfig


class ClientManager:
    def __init__(self, client_config: ClientConfig, logger, client_stop_event, client_started_event):
        self.client_config = client_config
        self.logger = logger
        self.client = None
        self.stop_event = client_stop_event
        self.is_client_running = client_started_event

    def start_client(self):
        self.client = Client(
            server=self.client_config.get_server_ip(),
            port=self.client_config.get_server_port(),
            use_ssl=self.client_config.get_use_ssl(),
            certfile=self.client_config.get_server_certfile(),
            logging=self.client_config.get_logging(),
            wait=self.client_config.get_wait(),
            stdout=self.logger.write
        )
        self.client.start()
        self.is_client_running.set()

    def stop_client(self):
        if self.client:
            self.client.stop()
            self.is_client_running.clear()

    def monitor_client(self):
        self.stop_event.wait()
        self.stop_client()

        # Clear status
        self.stop_event.clear()

        # Retrigger the start event to signal the client has stopped
        self.is_client_running.set()
        self.is_client_running.clear()
