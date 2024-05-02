from .iSocket import AbstractSocket


class WebSocket(AbstractSocket):
    def connect(self, host, port):
        self.sock.connect((host, port))

    def send(self, data):
        self.sock.send(data)

    def receive(self, buffer_size):
        return self.sock.recv(buffer_size)

    def close(self):
        self.sock.close()