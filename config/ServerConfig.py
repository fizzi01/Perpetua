import socket
from typing import Dict, Optional


class Client:
    """
    Class to manage the possible clients of the server.
    :param conn: Connection of the client.
    :param addr: Address of the client.
    :param key_map: Mapping of the keys pressed by the client. Es: {"ctrl": "cmd"}
    """

    def __init__(self, conn: socket.socket, addr: str, key_map: Dict[str, str]):
        self.conn: socket.socket | None = conn
        self.addr: str = addr
        self.key_map = key_map

    def get_connection(self) -> socket.socket:
        return self.conn

    def get_address(self) -> str:
        return self.addr

    def get_key_map(self) -> Dict[str, str]:
        return self.key_map

    def set_key_map(self, key_map: Dict[str, str]):
        self.key_map = key_map

    def get_key(self, key: str) -> str:
        return key if not self.key_map.get(key) else self.key_map.get(key)

    def set_connection(self, conn: socket.socket | None):
        self.conn = conn

    def set_address(self, addr: str):
        self.addr = addr


class Clients:
    """
    Class to manage the possible clients of the server.
    :param clients: Client.
    """

    def __init__(self, clients):
        self.clients: Dict[str, Client] = clients

    def get_client(self, position: str) -> Optional[socket.socket]:
        return self.clients.get(position)

    def set_client(self, position: str, client: Client):
        self.clients[position] = client

    def get_connection(self, position: str) -> Optional[socket.socket]:
        return self.clients[position].get_connection()

    def set_connection(self, position: str, conn: socket.socket):
        self.clients[position].set_connection(conn)

    def remove_connection(self, position: str):
        self.clients[position].set_connection(None)

    def get_possible_positions(self):
        return self.clients.keys()

    def get_address(self, position: str) -> str:
        return self.clients[position].get_address()

    def set_address(self, position: str, addr: str):
        self.clients[position].set_address(addr)


class ServerConfig:
    """
    Class to manage the configuration of the server.
    :param server_ip: IP of the server.
    :param server_port: Port of the server.
    :param clients: Clients of the server.
    """

    def __init__(self, server_ip: str, server_port: int, clients: Clients):
        self.server_ip = server_ip
        self.server_port = server_port
        self.clients = clients

    def get_server_ip(self) -> str:
        return self.server_ip

    def get_server_port(self) -> int:
        return self.server_port

    def get_clients(self) -> Clients:
        return self.clients

    def set_clients(self, clients: Clients):
        self.clients = clients

    def set_ip(self, ip: str):
        self.server_ip = ip

    def set_port(self, port: int):
        self.server_port = port
