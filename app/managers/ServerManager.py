from server.ServerBuilder import ServerBuilder


class ServerManager:
    def __init__(self, server_config, logger, server_stop_event, server_started_event):
        self.server_config = server_config
        self.logger = logger
        self.server = None
        self.stop_event = server_stop_event
        self.is_server_running = server_started_event

    def start_server(self):
        builder = ServerBuilder()
        self.server = ((builder.set_host(self.server_config.get_server_ip())
                       .set_port(self.server_config.get_server_port())
                       .set_clients(self.server_config.get_clients())
                       .set_wait(self.server_config.get_wait())
                       .set_logging(self.server_config.get_logging(), self.logger.write)
                       .set_screen_threshold(self.server_config.get_screen_threshold())
                       .enable_ssl(self.server_config.get_certfile(), self.server_config.get_keyfile()))
                       .build())

        self.server.start()
        self.is_server_running.set()

    def stop_server(self):
        if self.server:
            self.server.stop()
            self.is_server_running.clear()

    def monitor_server(self):
        self.stop_event.wait()
        self.stop_server()

        # Clear status
        self.stop_event.clear()

        # Retrigger the start event to signal the server has stopped
        self.is_server_running.set()
        self.is_server_running.clear()
