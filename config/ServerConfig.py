import socket
from typing import Dict, Optional

from utils.Interfaces import IBaseSocket, IClients, IClientObj


class Client(IClientObj):
    """
    Class to manage the possible clients of the server.
    :param conn: Connection of the client.
    :param addr: Address of the client.
    :param key_map: Mapping of the keys pressed by the client. Es: {"ctrl": "cmd"}
    """

    def __init__(self, conn: Optional[socket.socket | IBaseSocket] = None, addr: str = "", port: int = 5000,
                 key_map: Dict[str, str] = None):
        self.conn: Optional[socket.socket | IBaseSocket] = conn
        self.addr: str = addr
        self.port: int = port
        self.key_map = key_map if key_map is not None else {}
        self.screen_size = (0, 0)

    def get_screen_size(self) -> tuple:
        return self.screen_size

    def set_screen_size(self, size: tuple):
        self.screen_size = size

    def get_connection(self) -> Optional[socket.socket | IBaseSocket]:
        return self.conn

    def get_address(self) -> str:
        return self.addr

    def get_port(self) -> int:
        return self.port

    def get_key_map(self) -> Dict[str, str]:
        return self.key_map

    def set_key_map(self, key_map: Dict[str, str]):
        self.key_map = key_map

    def get_key(self, key: str) -> str:
        return self.key_map.get(key, key)

    def set_connection(self, conn: Optional[socket.socket | IBaseSocket]):
        self.conn = conn

    def set_address(self, addr: str):
        self.addr = addr

    def set_port(self, port: int):
        self.port = port

    def is_connected(self) -> bool:
        return self.conn is not None


class Clients(IClients):
    """
    Class to manage the possible clients of the server.
    :param clients: Dictionary of client positions and their associated Client objects.
    """

    def __init__(self, clients: Optional[Dict[str, IClientObj]] = None):
        self.clients: Dict[str, IClientObj] = clients if clients is not None else {}

    def get_client(self, position: str) -> Optional[IClientObj]:
        return self.clients.get(position, None)

    def set_client(self, position: str, client: IClientObj):
        self.clients[position] = client

    def get_connection(self, position: str) -> Optional[socket.socket | IBaseSocket]:
        client = self.get_client(position)
        return client.get_connection() if client else None

    def set_connection(self, position: str, conn: IBaseSocket):
        client = self.get_client(position)
        if client:
            client.set_connection(conn)

    def set_screen_size(self, position: str, size: tuple):
        client = self.get_client(position)
        if client:
            client.set_screen_size(size)

    def get_screen_size(self, position: str) -> tuple:
        client = self.get_client(position)
        return client.get_screen_size() if client else (0, 0)

    def remove_connection(self, position: str):
        client = self.get_client(position)
        if client:
            client.set_connection(None)

    def get_possible_positions(self):
        return self.clients.keys()

    def get_address(self, position: str) -> str:
        client = self.get_client(position)
        return client.get_address() if client else ""

    def get_position_by_address(self, addr: str) -> Optional[str]:
        for position, client in self.clients.items():
            if client.get_address() == addr:
                return position

    def set_address(self, position: str, addr: str):
        client = self.get_client(position)
        if client:
            client.set_address(addr)

    def remove_client(self, position: str):
        if position in self.clients:
            del self.clients[position]

    def get_connected_clients(self) -> Dict[str, IClientObj]:
        return {pos: client for pos, client in self.clients.items() if client.is_connected()}

    def __str__(self):
        return str(self.clients)


class ServerConfig:
    """
    Class to manage the configuration of the server.
    :param server_ip: IP of the server.
    :param server_port: Port of the server.
    :param clients: Clients of the server.
    """

    def __init__(self, server_ip: str, server_port: int, clients: Optional[Clients] = None, wait: int = 5,
                 screen_threshold: int = 10, logging: bool = True, use_ssl: bool = False, certfile: Optional[str] = None, keyfile: Optional[str] = None):
        self.server_ip = server_ip
        self.server_port = server_port
        self.clients = clients if clients is not None else Clients()

        self.wait = wait
        self.screen_threshold = screen_threshold
        self.logging = logging
        self.use_ssl = use_ssl

        self.certfile = certfile
        self.keyfile = keyfile

    def get_certfile(self) -> Optional[str]:
        return self.certfile

    def get_keyfile(self) -> Optional[str]:
        return self.keyfile

    def set_certfile(self, certfile: Optional[str]):
        self.certfile = certfile

    def set_keyfile(self, keyfile: Optional[str]):
        self.keyfile = keyfile

    def get_use_ssl(self) -> bool:
        return self.use_ssl

    def get_wait(self) -> int:
        return self.wait

    def get_screen_threshold(self) -> int:
        return self.screen_threshold

    def get_logging(self) -> bool:
        return self.logging

    def set_wait(self, wait: int):
        self.wait = wait

    def set_screen_threshold(self, screen_threshold: int):
        self.screen_threshold = screen_threshold

    def set_logging(self, logging: bool):
        self.logging = logging

    def set_use_ssl(self, use_ssl: bool):
        self.use_ssl = use_ssl

    def get_server_ip(self) -> str:
        return self.server_ip

    def get_server_port(self) -> int:
        return self.server_port

    def get_clients(self) -> Clients:
        return self.clients

    def set_clients(self, clients: Clients):
        self.clients = clients

    def set_server_ip(self, ip: str):
        self.server_ip = ip

    def set_server_port(self, port: int):
        self.server_port = port
