from client.ClientBuilder import ClientBuilder
from config.ClientConfig import ClientConfig


class ClientManager:
    def __init__(self, client_config: ClientConfig, logger, client_stop_event, client_started_event):
        self.client_config = client_config
        self.logger = logger
        self.client = None
        self.stop_event = client_stop_event
        self.is_client_running = client_started_event

    def start_client(self):
        builder = ClientBuilder()
        self.client = (builder.set_host(self.client_config.get_server_ip())
                       .set_port(self.client_config.get_server_port())
                       .enable_ssl(self.client_config.get_server_certfile(), None)
                       .set_logging(self.client_config.get_logging(), self.logger.write)
                       .set_wait(self.client_config.get_wait())
                          .build())

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
