import socket
import asyncio
import websockets
from abc import ABC, abstractmethod
import json


# Base class for socket management
class BaseSocket(ABC):
    def __init__(self, host: str, port: int, wait: int):
        self.host = host
        self.port = port
        self.wait = wait

    @abstractmethod
    def bind_and_listen(self):
        pass

    @abstractmethod
    def accept(self):
        pass

    @abstractmethod
    def send(self, data):
        pass

    @abstractmethod
    def close(self):
        pass

    @abstractmethod
    def is_socket_open(self):
        pass


# TCP Socket implementation
class TCPSocket(BaseSocket):
    def __init__(self, host: str, port: int, wait: int):
        super().__init__(host, port, wait)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(wait)

    def bind_and_listen(self):
        self.socket.bind((self.host, self.port))
        self.socket.listen()

    def accept(self):
        return self.socket.accept()

    def close(self):
        self.socket.close()

    def send(self, data):
        self.socket.send(data)

    def is_socket_open(self):
        try:
            self.socket.getsockname()
            return True
        except socket.error:
            return False


# WebSocket implementation
class WebSocketServer(BaseSocket):
    def __init__(self, host: str, port: int, wait: int):
        super().__init__(host, port, wait)
        self.websocket = None
        self.connected_clients = set()

    async def start_server(self):
        self.websocket = await websockets.serve(self.handle_connection, self.host, self.port)

    async def handle_connection(self, websocket, path):
        self.connected_clients.add(websocket)
        try:
            await self.secure_handshake(websocket, path)
            await self.send(websocket)
        finally:
            self.connected_clients.remove(websocket)

    async def secure_handshake(self, websocket, path):
        try:
            config_data = {
                "host": self.host,
                "port": self.port,
                "wait": self.wait
            }
            await websocket.send(json.dumps(config_data))
            client_response = await websocket.recv()
            client_config = json.loads(client_response)
            print(f"Received client configuration: {client_config}")
        except Exception as e:
            print(f"Handshake failed: {e}")

    async def send(self, websocket):
        try:
            async for message in websocket:
                print(f"Received message from client: {message}")
                await websocket.send(f"Echo: {message}")
        except websockets.exceptions.ConnectionClosed as e:
            print(f"Connection closed: {e}")

    def bind_and_listen(self):
        asyncio.run(self.start_server())

    def accept(self):
        raise NotImplementedError("WebSocket does not support accept method directly")

    def close(self):
        if self.websocket is not None:
            for client in self.connected_clients:
                asyncio.run(client.close())
            self.websocket.ws_server.close()

    def is_socket_open(self):
        return self.websocket is not None


# Factory Pattern to create socket types
class SocketFactory:

    TCP = "TCP"
    WEBSOCKET = "WebSocket"

    @staticmethod
    def create_socket(socket_type: str, host: str, port: int, wait: int) -> BaseSocket:
        if socket_type == "TCP":
            return TCPSocket(host, port, wait)
        elif socket_type == "WebSocket":
            return WebSocketServer(host, port, wait)
        else:
            raise ValueError(f"Unsupported socket type: {socket_type}")


# Singleton per il socket del server
class ServerSocket:
    _instance = None

    def __new__(cls, host: str, port: int, wait: int):
        if cls._instance is None or not cls._instance.is_socket_open():
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

    def pause(self):
        self.socket.detach()

    def close(self):
        self.socket.close()

    def is_socket_open(self):
        try:
            self.socket.getsockname()
            return True
        except socket.error:
            return False
