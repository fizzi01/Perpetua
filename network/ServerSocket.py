import socket


# Singleton per il socket del server
class ServerSocket:
    _instance = None

    def __new__(cls, host: str, port: int, wait: int):
        if cls._instance is None:
            cls._instance = super(ServerSocket, cls).__new__(cls)
            cls._instance._initialize_socket(host, port, wait)
        return cls._instance

    def _initialize_socket(self, host: str, port: int, wait: int):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(wait)
        self.host = host
        self.port = port

    def bind_and_listen(self):
        self.socket.bind((self.host, self.port))
        self.socket.listen()

    def accept(self):
        return self.socket.accept()

    def close(self):
        self.socket.close()
