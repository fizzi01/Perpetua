"""
Provides an object representation of a client (connected to the server).
Information includes IP address, port, connection time,
and other metadata like screen position relative to the server (center),
screen resolution, and client name. But also additional optional config parameters (future use).
"""


#  Perpetua - open-source and cross-platform KVM software.
#  Copyright (c) 2026 Federico Izzi.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

import re
from enum import StrEnum

from typing import Optional

from model.connection import ClientConnection
from model.monitor import MonitorInfo

# Compiled once: hostname validation runs on every client (dis)connect.
_HOSTNAME_LABEL_RE = re.compile(r"(?!-)[A-Z\d-]{1,63}(?<!-)$", re.IGNORECASE)


class ScreenPosition(StrEnum):
    """
    Enumeration of different screen positions.
    """

    CENTER = "center"
    TOP = "top"
    BOTTOM = "bottom"
    LEFT = "left"
    RIGHT = "right"
    UKNOWN = "unknown"
    NONE = "none"

    @classmethod
    def is_valid(cls, position: Optional[str]) -> bool:
        """
        Verify if the given screen position is valid.
        """
        if position is None:
            return True

        try:
            cls(position)
            return True
        except ValueError:
            return False


class ClientObj:
    """
    Represents a client with its metadata.

    A client can have multiple known IP addresses (e.g. due to DHCP or multi-homed hosts)
    under the same uid and hostname. The ``ip_addresses`` list stores all known IPs,
    while ``ip_address`` (property) returns the current/active IP used in the connection.
    """

    def __init__(
        self,
        uid: Optional[str] = None,
        ip_addresses: Optional[list[str] | str] = None,
        hostname: Optional[str] = None,
        ports: Optional[dict[int, int]] = None,
        first_connection_date: Optional[str] = None,
        last_connection_date: Optional[str] = None,
        is_connected: bool = False,
        screen_position: str = ScreenPosition.CENTER,
        screen_resolution: str = "1x1",
        ssl: bool = False,
        conn_socket: Optional[ClientConnection] = None,
        additional_params: Optional[dict] = None,
        monitors: Optional[list[dict | MonitorInfo]] = None,
        placements: Optional[list[dict]] = None,
    ):
        self.uid = uid

        if hostname is not None and not self._check_hostname(hostname):
            raise ValueError(f"Invalid hostname: {hostname}")
        self.host_name = hostname

        # Normalize ip_addresses: accept a single str (backward compat) or a list
        if isinstance(ip_addresses, str):
            ip_addresses = [ip_addresses]
        self.ip_addresses: list[str] = list(ip_addresses) if ip_addresses else []

        # Validate all IPs (only when hostname is not set as fallback)
        if not self.host_name:
            for ip in self.ip_addresses:
                if not self._check_ip(ip):
                    raise ValueError(f"Invalid IP address in ip_addresses: {ip}")

        # Current/active IP used in the connection (None when not connected)
        self._current_ip: Optional[str] = None

        self.open_streams = ports if ports is not None else {}
        self.first_connection_date = first_connection_date
        self.last_connection_date = last_connection_date

        self.screen_position = screen_position
        if not ScreenPosition.is_valid(screen_position):
            raise ValueError(f"Invalid screen position: {screen_position}")

        self.screen_resolution = screen_resolution
        # Serialized per-monitor info sent by the client during handshake
        # (list of MonitorInfo.to_dict()-style dicts). Empty list means
        # the client did not advertise any monitor list (legacy / pre-
        # multi-monitor build): callers fall back to ``screen_resolution``.
        # Kept as plain dicts here so the field survives JSON / msgpack
        # round-trips without importing ``utils.screen`` in the model
        # layer; consumers build :class:`MonitorInfo` on demand.
        self.monitors: list[dict | MonitorInfo] = list(monitors) if monitors else []
        # Per-monitor placements in the SERVER's virtual workspace.
        # Each dict shape (mirrors the GUI's ``MonitorPlacement``):
        #   {
        #     "client_monitor_id": int,   # index into self.monitors
        #     "workspace_x": int,         # top-left in server workspace coords
        #     "workspace_y": int,
        #     "width": int,
        #     "height": int,
        #   }
        # The runtime adjacency (which server-monitor edge crosses INTO
        # which of this client's monitors and over which axis range) is
        # NOT stored here — it's derived on demand via
        # :meth:`get_edge_bindings` so the source of truth stays simple.
        self.placements: list[dict] = list(placements) if placements else []
        self.ssl = ssl
        self.conn_socket = conn_socket
        self.is_connected = is_connected
        self.additional_params = (
            additional_params if additional_params is not None else {}
        )

    # --- IP address helpers ---

    @property
    def ip_address(self) -> Optional[str]:
        """Returns the current/active IP, falling back to the first known IP."""
        if self._current_ip is not None:
            return self._current_ip
        return self.ip_addresses[0] if self.ip_addresses else None

    @ip_address.setter
    def ip_address(self, value: Optional[str]) -> None:
        """Sets the current/active IP and ensures it is in the known list."""
        self._current_ip = value
        if value is not None and value not in self.ip_addresses:
            self.ip_addresses.append(value)

    def has_ip(self, ip: str) -> bool:
        """Check if the given IP is among the known addresses for this client."""
        return ip in self.ip_addresses

    def add_ip(self, ip: str) -> None:
        """Add a new IP to the list of known addresses (no duplicates)."""
        if ip not in self.ip_addresses:
            if not self._check_ip(ip):
                raise ValueError(f"Invalid IP address: {ip}")
            self.ip_addresses.append(ip)

    def set_connection_status(self, status: bool) -> None:
        """
        Sets the connection status of the client.
        Args:
            status: A boolean indicating the connection status to set.
        """
        self.is_connected = status

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

    def get_connection(self) -> Optional["ClientConnection"]:
        """
        Returns the connection socket associated with the client.
        """
        return self.conn_socket

    def set_connection(self, connection: Optional["ClientConnection"]) -> None:
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
        if len(hostname) > 255:
            return False
        if hostname[-1] == ".":
            hostname = hostname[:-1]
        return all(_HOSTNAME_LABEL_RE.match(x) for x in hostname.split("."))

    def get_net_id(self) -> Optional[str]:
        """
        Returns a unique identifier for the client, prioritizing hostname over IP address.
        """
        return self.host_name if self.host_name is not None else self.ip_address

    def get_effective_placements(self, server_monitors) -> list[dict]:
        """Return the placements that should drive cross-screen routing.

        If the client has been positioned on the workspace via the
        layout editor, return ``self.placements`` verbatim. Otherwise
        synthesize a single 1:1 placement next to the server's primary
        monitor on the side indicated by the legacy ``screen_position``
        — that way pre-layout clients keep working through the unified
        spatial routing instead of a parallel ``ScreenPosition`` code
        path. Returns an empty list only when the legacy position is
        ``CENTER`` / unset or the server has no monitors to anchor to.
        """
        if self.placements:
            return list(self.placements)

        if not server_monitors:
            return []
        primary = next(
            (m for m in server_monitors if getattr(m, "is_primary", False)),
            server_monitors[0],
        )
        pw = primary.max_x - primary.min_x
        ph = primary.max_y - primary.min_y
        if pw <= 0 or ph <= 0:
            return []
        # Use the client's primary monitor dims when known so the
        # synthetic placement keeps the right aspect ratio for the
        # client-side denormalisation. Fall back to the server primary's
        # size for legacy clients that haven't advertised a monitor list.
        cw, ch = pw, ph
        cm_id = 0
        if self.monitors:
            cm = next(
                (m for m in self.monitors if getattr(m, "is_primary", False)),
                self.monitors[0],
            )
            if isinstance(cm, dict):
                cm = MonitorInfo.from_dict(cm)

            try:
                cw = max(1, cm.max_x - cm.min_x)
                ch = max(1, cm.max_y - cm.min_y)
                cm_id = cm.monitor_id
            except (AttributeError, KeyError):
                print("Warning: Client monitor info is missing expected fields. "
                      "Falling back to server primary monitor size.")
                pass

        pos = (self.screen_position or "").lower()
        if pos == ScreenPosition.LEFT:
            wx, wy = primary.min_x - cw, primary.min_y
        elif pos == ScreenPosition.RIGHT:
            wx, wy = primary.max_x, primary.min_y
        elif pos == ScreenPosition.TOP:
            wx, wy = primary.min_x, primary.min_y - ch
        elif pos == ScreenPosition.BOTTOM:
            wx, wy = primary.min_x, primary.max_y
        else:
            return []
        return [{
            "client_monitor_id": cm_id,
            "workspace_x": wx,
            "workspace_y": wy,
            "width": cw,
            "height": ch,
        }]

    def get_edge_bindings(self, server_monitors) -> list:
        """Derive the per-placement :class:`EdgeBinding` list from this
        client's effective placements (real or synthesized from
        ``screen_position``) and the given ``server_monitors`` list.

        The unified bindings drive routing in BOTH directions: the
        server side reads ``server_*`` fields, the client side reads
        ``client_*`` fields. Empty only when both placements and the
        legacy position are unset.

        Local import to avoid pulling ``utils.screen`` into the model
        layer at module load.
        """
        placements = self.get_effective_placements(server_monitors)
        if not placements:
            return []
        from utils.screen import compute_edge_bindings

        out: list = []
        for p in placements:
            out.extend(compute_edge_bindings(p, server_monitors))
        return out

    def to_dict(self) -> dict:
        return self.__dict__()

    @staticmethod
    def from_dict(data: dict) -> "ClientObj":
        # Backward compat: support both legacy "ip_address" (str) and "ip_addresses" (list)
        ip_addresses = data.get("ip_addresses", None)
        if ip_addresses is None:
            # Fallback to legacy single-IP field
            legacy_ip = data.get("ip_address", None)
            if isinstance(legacy_ip, str):
                ip_addresses = [legacy_ip]

        return ClientObj(
            uid=data.get("uid"),
            hostname=data.get("host_name", None),
            ip_addresses=ip_addresses,
            first_connection_date=data.get("first_connection_date", None),
            last_connection_date=data.get("last_connection_date", None),
            screen_position=data.get("screen_position", ScreenPosition.CENTER),
            screen_resolution=data.get("screen_resolution", "1x1"),
            ssl=data.get("ssl", False),
            additional_params=data.get("additional_params", {}),
            monitors=data.get("monitors", []),
            placements=data.get("placements", []),
        )

    def __dict__(self) -> dict:
        return {
            "uid": self.uid,
            "host_name": self.host_name,
            "ip_addresses": list(self.ip_addresses),
            "first_connection_date": self.first_connection_date,
            "last_connection_date": self.last_connection_date,
            "screen_position": self.screen_position,
            "screen_resolution": self.screen_resolution,
            "ssl": self.ssl,
            "is_connected": self.is_connected,
            "additional_params": self.additional_params,
            "monitors": list(self.monitors),
            "placements": list(self.placements),
        }

    def __repr__(self):
        return (
            f"ClientObj(uid={self.uid}, "
            f"host_name={self.host_name}, ip_addresses={self.ip_addresses}, "
            f"current_ip={self.ip_address}, port={self.open_streams}, "
            f"screen_position={self.screen_position}, "
            f"screen_resolution={self.screen_resolution}, "
            f"additional_params={self.additional_params})"
        )


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

    def update_client(self, client: "ClientObj") -> "ClientsManager":
        """
        Update existing client info. Matches by uid first, then hostname,
        then by overlapping IP addresses.
        """
        for idx, existing_client in enumerate(self.clients):
            # Match by uid (strongest identity)
            if client.uid and existing_client.uid and existing_client.uid == client.uid:
                self.clients[idx] = client
                return self
            # Match by hostname
            if (
                client.host_name
                and existing_client.host_name
                and existing_client.host_name == client.host_name
            ):
                self.clients[idx] = client
                return self
            # Match by any overlapping IP
            if any(ip in existing_client.ip_addresses for ip in client.ip_addresses):
                self.clients[idx] = client
                return self
        raise ValueError("Client not found to update.")

    def add_client(self, client: "ClientObj") -> "ClientsManager":
        """Register a client, enforcing identity uniqueness only.

        Post-migration to free-form 2D placements, the number of
        clients is no longer capped at 4 (one per ScreenPosition).
        Uniqueness is enforced on the **identity** axes that actually
        identify a client: UID (when known) and the hostname/IP tuple
        used during discovery. Spatial placement overlaps are checked
        separately in :meth:`Server.set_client_layout` where the
        admin actively positions the client.

        Two unplaced clients sharing the legacy ``screen_position``
        (e.g. both arriving with the historical ``"top"`` default) are
        allowed: routing happens through the placement-derived
        :class:`EdgeBinding` cache, and the synthesized fallback in
        :meth:`ClientObj.get_effective_placements` would still pick
        one consistently.
        """
        for existing_client in self.clients:
            if (
                client.uid
                and existing_client.uid
                and existing_client.uid == client.uid
            ):
                raise ValueError(
                    f"Client with uid '{client.uid}' already exists."
                )
            if (
                client.host_name
                and existing_client.host_name
                and existing_client.host_name == client.host_name
                and any(
                    ip in existing_client.ip_addresses
                    for ip in client.ip_addresses
                )
            ):
                raise ValueError(
                    f"Client '{client.host_name}' with overlapping IPs "
                    f"already exists."
                )
        self.clients.append(client)
        return self

    def remove_client(
        self, client: Optional["ClientObj"], position: Optional[str] = None
    ) -> "ClientsManager":
        if client:
            self.clients = [c for c in self.clients if c != client]
        elif position:
            self.clients = [c for c in self.clients if c.screen_position != position]
        else:
            raise ValueError(
                "Either client or position must be provided to remove a client."
            )
        return self

    def clear(self):
        self.clients = []

    def get_clients(self) -> list["ClientObj"]:
        return self.clients

    def get_client(
        self,
        ip_address: Optional[str] = None,
        hostname: Optional[str] = None,
        screen_position: Optional[str] = None,
        uid: Optional[str] = None,
    ) -> Optional["ClientObj"]:
        """
        Look a client up by one of: UID, hostname, IP address,
        ``screen_position`` (legacy). UID takes precedence — it's the
        stable identifier used by the bus / routing layer post-migration.
        Hostname, IP and ``screen_position`` are kept for legacy paths
        (config lookups, hand-rolled CLI tooling, etc.).

        When the manager is in client mode it always returns the sole
        client regardless of the filter.

        Returns ``None`` when no match is found.
        """

        if self._is_client_main:  # Return the only client in client mode
            return self.clients[0] if self.clients else None

        for client in self.clients:
            if uid:
                if client.uid == uid:
                    return client
                continue
            if hostname:  # Prioritize hostname if provided
                if client.host_name and client.host_name == hostname:
                    return client

            if ip_address:
                # Check against the list of known IP addresses
                if client.has_ip(ip_address):
                    return client
            elif screen_position:
                if client.screen_position == screen_position:
                    return client
        return None
