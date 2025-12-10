"""
Provides an object representation of a client (connected to the server).
Information includes IP address, port, connection time,
and other metadata like screen position relative to the server (center),
screen resolution, and client name. But also additional optional config parameters (future use).
"""
from typing import Optional

class ClientObj:
    """
    Represents a client with its metadata.
    """
    def __init__(self,
                 ip_address: str,
                 hostname: Optional[str] = None,
                 ports: dict[int, int] = None,
                 connection_time: float = 0.0,
                 is_connected: bool = False,
                 screen_position: str = "center",
                 screen_resolution: str = "1920x1080",
                 client_name: str = "Unknown",
                 ssl: bool = False,
                 conn_socket: Optional[object] = None,
                 additional_params: dict = None):
        self.host_name = hostname
        self.ip_address = ip_address
        self.ports = ports if ports is not None else {}
        self.connection_time = connection_time
        self.screen_position = screen_position
        self.screen_resolution = screen_resolution
        self.client_name = client_name
        self.ssl = ssl
        self.conn_socket = conn_socket
        self.is_connected = is_connected
        self.additional_params = additional_params if additional_params is not None else {}

    def get_net_id(self) -> str:
        return self.host_name if self.host_name else self.ip_address


    def __repr__(self):
        return (f"ClientObj(host_name={self.host_name}, ip_address={self.ip_address}, port={self.ports}, "
                f"connection_time={self.connection_time}, screen_position={self.screen_position}, "
                f"screen_resolution={self.screen_resolution}, client_name={self.client_name}, "
                f"additional_params={self.additional_params})")


class ClientsManager:
    """
    Manages multiple ClientObj instances.
    Provides methods to add, remove, and retrieve clients.
    """

    def __init__(self, client_mode: bool = False):
        """
        If client_mode is True, the manager is in client mode and will handle only a single main client.
        """
        self.clients = []
        self._is_client_main = client_mode

    def update_client(self, client: 'ClientObj') -> 'ClientsManager':
        # Update existing client info based on IP and port
        for idx, existing_client in enumerate(self.clients):
            if existing_client.ip_address == client.ip_address:
                self.clients[idx] = client
                return self
        raise ValueError("Client not found to update.")

    def add_client(self, client: 'ClientObj') -> 'ClientsManager':
        """
        Avoids screen_position duplication when adding a new client.
        """
        for existing_client in self.clients:
            if existing_client.screen_position == client.screen_position:
                raise ValueError(f"Client with screen position '{client.screen_position}' already exists.")
        self.clients.append(client)
        return self

    def remove_client(self, client: Optional['ClientObj'], position: Optional[str] = None) -> 'ClientsManager':
        if client:
            self.clients = [c for c in self.clients if c != client]
        elif position:
            self.clients = [c for c in self.clients if c.screen_position != position]
        else:
            raise ValueError("Either client or position must be provided to remove a client.")
        return self

    def get_clients(self) -> list['ClientObj']:
        return self.clients

    def get_client(self, ip_address: Optional[str] = None, hostname: Optional[str] = None, screen_position: Optional[str] = None) -> Optional['ClientObj']:

        if self._is_client_main: # Return the only client in client mode
            return self.clients[0] if self.clients else None

        for client in self.clients:
            if hostname: #Prioritize hostname if provided
                if client.host_name and client.host_name == hostname:
                    return client
            elif ip_address:
                if client.ip_address == ip_address:
                    return client
            elif screen_position:
                if client.screen_position == screen_position:
                    return client
            # else:
            #     raise ValueError("Either ip_address or screen_position must be provided to get a client.")
        return None