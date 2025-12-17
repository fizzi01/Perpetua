"""
Provides an object representation of a client (connected to the server).
Information includes IP address, port, connection time,
and other metadata like screen position relative to the server (center),
screen resolution, and client name. But also additional optional config parameters (future use).
"""
from typing import Optional

from network.connection import ClientConnection

class ScreenPosition:
    """
    Enum-like class for screen positions.
    """
    CENTER = "center"
    TOP = "top"
    BOTTOM = "bottom"
    LEFT = "left"
    RIGHT = "right"
    UKNOWN = "unknown"
    NONE = None

    @staticmethod
    def is_valid(position: Optional[str]) -> bool:
        return position.lower() in {
            ScreenPosition.CENTER,
            ScreenPosition.TOP,
            ScreenPosition.BOTTOM,
            ScreenPosition.LEFT,
            ScreenPosition.RIGHT,
        }

class ClientObj:
    """
    Represents a client with its metadata.
    """
    def __init__(self,
                 ip_address: Optional[str] = None,
                 hostname: Optional[str] = None,
                 ports: dict[int, int] = None,
                 connection_time: float = 0.0, # FixME: Use datetime and first and last. This connection_time can increase indefinitely !!!
                 first_connection_date: str = None,
                 last_connection_date: str = None,
                 is_connected: bool = False,
                 screen_position: str = ScreenPosition.CENTER,
                 screen_resolution: str = "1x1",
                 client_name: str = "Unknown",
                 ssl: bool = False,
                 conn_socket: Optional['ClientConnection'] = None,
                 additional_params: dict = None):

        if hostname and not self._check_hostname(hostname):
            raise ValueError(f"Invalid hostname: {hostname}")
        self.host_name = hostname

        # We prioritize hostname validation over IP address validation
        if ip_address and not self._check_ip(ip_address) and not self.host_name:
            raise ValueError(f"Invalid IP address: {ip_address}")
        self.ip_address = ip_address

        self.ports = ports if ports is not None else {}
        self.connection_time = connection_time
        self.first_connection_date = first_connection_date
        self.last_connection_date = last_connection_date

        self.screen_position = screen_position
        if not ScreenPosition.is_valid(screen_position):
            raise ValueError(f"Invalid screen position: {screen_position}")

        self.screen_resolution = screen_resolution
        self.client_name = client_name
        self.ssl = ssl
        self.conn_socket = conn_socket
        self.is_connected = is_connected
        self.additional_params = additional_params if additional_params is not None else {}

    def set_first_connection(self):
        """
        Sets the first connection date for the current instance. If the first connection date
        is already set, the function exits without making changes. Otherwise, it records
        the current date and time in the format "YYYY-MM-DD HH:MM:SS".

        Raises:
            None

        Returns:
            None
        """
        if self.first_connection_date is not None:
            return
        from datetime import datetime
        self.first_connection_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def set_last_connection(self):
        from datetime import datetime
        self.last_connection_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def get_connection(self) -> Optional['ClientConnection']:
        """
        Returns the connection socket associated with the client.
        """
        return self.conn_socket

    def set_connection(self, connection: Optional['ClientConnection']) -> None:
        """
        Sets the connection socket for the client.
        """
        self.conn_socket = connection

    def get_screen_position(self) -> str:
        """
        Returns the screen position of the client.
        """
        return self.screen_position

    def set_screen_position(self, screen_position: str) -> None:
        """
        Sets the screen position of the client.

        Args:
            screen_position: The new screen position to set.

        Raises:
            ValueError: If the provided screen position is invalid.
        """
        if not ScreenPosition.is_valid(screen_position):
            raise ValueError(f"Invalid screen position: {screen_position}")
        self.screen_position = screen_position

    @staticmethod
    def _check_ip(ip_address: str) -> bool:
        """
        Validates the given IP address format (IPv4 or IPv6).

        Args:
            ip_address: The IP address string to validate.

        Returns:
            A boolean indicating whether the IP address is valid (True) or invalid (False).
        """
        import ipaddress
        try:
            ipaddress.ip_address(ip_address)
            return True
        except ValueError:
            return False

    @staticmethod
    def _check_hostname(hostname: str) -> bool:
        """
        Checks if the given hostname is valid according to common domain naming conventions.

        This method verifies that the hostname conforms to the general rules for domain
        names, such as length restrictions and valid character usage. It ensures that
        the hostname does not exceed 255 characters, does not end with a period unless
        trimmed, and separates its parts by dots, each conforming to specific length
        and naming requirements.

        Args:
            hostname: The hostname string to validate.

        Returns:
            A boolean indicating whether the hostname is valid (True) or invalid (False).
        """
        import re
        if len(hostname) > 255:
            return False
        if hostname[-1] == ".":
            hostname = hostname[:-1]
        allowed = re.compile(r"(?!-)[A-Z\d-]{1,63}(?<!-)$", re.IGNORECASE)
        return all(allowed.match(x) for x in hostname.split("."))

    def get_net_id(self) -> str:
        """
        Returns a unique identifier for the client, prioritizing hostname over IP address.
        """
        return self.host_name if self.host_name else self.ip_address

    def to_dict(self) -> dict:
        return self.__dict__()

    @classmethod
    def from_dict(cls, data: dict) -> 'ClientObj':
        c = cls()
        c.host_name = data.get("host_name", None)
        c.ip_address = data.get("ip_address", None)
        c.connection_time = data.get("connection_time", 0.0)
        c.first_connection_date = data.get("first_connection_date", None)
        c.last_connection_date = data.get("last_connection_date", None)
        c.screen_position = data.get("screen_position", ScreenPosition.CENTER)
        c.screen_resolution = data.get("screen_resolution", "1x1")
        c.ssl = data.get("ssl", False)
        c.additional_params = data.get("additional_params", {})
        return c

    def __dict__(self) -> dict:
        return {
            "host_name": self.host_name,
            "ip_address": self.ip_address,
            "connection_time": self.connection_time,
            "first_connection_date": self.first_connection_date,
            "last_connection_date": self.last_connection_date,
            "screen_position": self.screen_position,
            "screen_resolution": self.screen_resolution,
            "ssl": self.ssl,
            "additional_params": self.additional_params
        }

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

    def clear(self):
        self.clients = []

    def get_clients(self) -> list['ClientObj']:
        return self.clients

    def get_client(self, ip_address: Optional[str] = None, hostname: Optional[str] = None, screen_position: Optional[str] = None) -> Optional['ClientObj']:
        """
        Returns a specific client from the list of available clients based on the given criteria.
        The method primarily supports client filtering by `hostname`, `ip_address`, or `screen_position`.
        When the mode is client mode, it will return the first client if one exists since there should
        only be one client in this mode.

        If multiple criteria are provided, the method gives priority to `hostname` followed by `ip_address`
        and then `screen_position`.

        Parameters:
            ip_address (Optional[str]): The IP address of the desired client. Used for filtering if provided.
            hostname (Optional[str]): The hostname of the desired client. If present, this filter is
                prioritized over other criteria.
            screen_position (Optional[str]): The screen position of the desired client. Considered if
                other filters are not specified.

        Returns:
            Optional[ClientObj]: The client object matching the given criteria, or `None` if no client
            matches the provided conditions or if no clients exist.
        """

        if self._is_client_main: # Return the only client in client mode
            return self.clients[0] if self.clients else None

        for client in self.clients:
            if hostname: #Prioritize hostname if provided
                if client.host_name and client.host_name == hostname:
                    return client

            if ip_address:
                if client.ip_address == ip_address:
                    return client
            elif screen_position:
                if client.screen_position == screen_position:
                    return client
            # else:
            #     raise ValueError("Either ip_address or screen_position must be provided to get a client.")
        return None