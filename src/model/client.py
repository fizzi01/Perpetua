"""Object representation of a client connected to the server."""


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
    """Enumeration of screen positions."""

    CENTER = "center"
    TOP = "top"
    BOTTOM = "bottom"
    LEFT = "left"
    RIGHT = "right"
    UKNOWN = "unknown"
    NONE = "none"

    @classmethod
    def is_valid(cls, position: Optional[str]) -> bool:
        if position is None:
            return True
        try:
            cls(position)
            return True
        except ValueError:
            return False


class ClientObj:
    """Represents a client with its metadata.

    A client can carry multiple known IP addresses (DHCP, multi-homed
    hosts) under the same uid/hostname. ``ip_addresses`` stores the
    full set; the ``ip_address`` property returns the active one.
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
        # Accept raw dicts so legacy config files and the network
        # ingress path round-trip without forcing call sites to convert.
        # The rest of the pipeline assumes attribute access.
        def _coerce_monitors(
            raw: Optional[list[dict | MonitorInfo]],
        ) -> list[MonitorInfo]:
            if not raw:
                return []
            out: list[MonitorInfo] = []
            for m in raw:
                if isinstance(m, MonitorInfo):
                    out.append(m)
                elif isinstance(m, dict):
                    try:
                        out.append(MonitorInfo.from_dict(m))
                    except (KeyError, TypeError, ValueError):
                        continue
            return out

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
        # Per-monitor info advertised by the client during handshake.
        # Empty = legacy client that didn't advertise its layout;
        # fallback is ``screen_resolution``.
        self.monitors: list[MonitorInfo] = _coerce_monitors(monitors)
        # Per-monitor placements in the SERVER's virtual workspace, each
        # dict of shape
        # ``{client_monitor_id, workspace_x, workspace_y, width, height}``.
        # Runtime adjacency is derived on demand via
        # :meth:`get_edge_bindings` so the source of truth stays simple.
        self.placements: list[dict] = list(placements) if placements else []
        self.ssl = ssl
        self.conn_socket = conn_socket
        self.is_connected = is_connected
        self.additional_params = (
            additional_params if additional_params is not None else {}
        )

    @property
    def ip_address(self) -> Optional[str]:
        """Current/active IP, falling back to the first known IP."""
        if self._current_ip is not None:
            return self._current_ip
        return self.ip_addresses[0] if self.ip_addresses else None

    @ip_address.setter
    def ip_address(self, value: Optional[str]) -> None:
        self._current_ip = value
        if value is not None and value not in self.ip_addresses:
            self.ip_addresses.append(value)

    def has_ip(self, ip: str) -> bool:
        return ip in self.ip_addresses

    def add_ip(self, ip: str) -> None:
        if ip not in self.ip_addresses:
            if not self._check_ip(ip):
                raise ValueError(f"Invalid IP address: {ip}")
            self.ip_addresses.append(ip)

    def set_connection_status(self, status: bool) -> None:
        self.is_connected = status

    def set_first_connection(self):
        if self.first_connection_date is not None:
            return
        from datetime import datetime

        self.first_connection_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def set_last_connection(self):
        from datetime import datetime

        self.last_connection_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def get_connection(self) -> Optional["ClientConnection"]:
        return self.conn_socket

    def set_connection(self, connection: Optional["ClientConnection"]) -> None:
        self.conn_socket = connection

    def get_screen_position(self) -> str:
        return self.screen_position

    def set_screen_position(self, screen_position: str) -> None:
        if not ScreenPosition.is_valid(screen_position):
            raise ValueError(f"Invalid screen position: {screen_position}")
        self.screen_position = screen_position

    @staticmethod
    def _check_ip(ip_address: str) -> bool:
        import ipaddress

        try:
            ipaddress.ip_address(ip_address)
            return True
        except ValueError:
            return False

    @staticmethod
    def _check_hostname(hostname: str) -> bool:
        if len(hostname) > 255:
            return False
        if hostname[-1] == ".":
            hostname = hostname[:-1]
        return all(_HOSTNAME_LABEL_RE.match(x) for x in hostname.split("."))

    def get_net_id(self) -> Optional[str]:
        """Stable id: hostname if known, otherwise active IP."""
        return self.host_name if self.host_name is not None else self.ip_address

    def get_effective_placements(self, server_monitors) -> list[dict]:
        """Return the placements that drive cross-screen routing.

        1. Explicit set: use ``self.placements`` if populated; otherwise
           synthesize a single placement from the legacy ``screen_position``.
        2. Auto-derivation: for any client monitor missing from the
           explicit set, append a derived placement using its OS-relative
           offset from the first explicit placement's anchor. The
           workspace inherits the client's OS topology by default;
           admins override by placing all monitors explicitly.
        """
        if self.placements:
            explicit = list(self.placements)
        else:
            explicit = self._synthesize_legacy_placement(server_monitors)

        if not explicit or not self.monitors:
            return explicit

        # First explicit placement is the deterministic anchor for
        # OS-relative derivation.
        anchor_placement = explicit[0]
        try:
            anchor_id = int(anchor_placement["client_monitor_id"])
        except (KeyError, TypeError, ValueError):
            return explicit
        anchor_monitor = next(
            (m for m in self.monitors if m.monitor_id == anchor_id),
            None,
        )
        if anchor_monitor is None:
            return explicit

        placed_ids: set[int] = set()
        for p in explicit:
            try:
                placed_ids.add(int(p["client_monitor_id"]))
            except (KeyError, TypeError, ValueError):
                continue

        try:
            anchor_wx = int(anchor_placement["workspace_x"])
            anchor_wy = int(anchor_placement["workspace_y"])
        except (KeyError, TypeError, ValueError):
            return explicit

        derived: list[dict] = []
        for m in self.monitors:
            if m.monitor_id in placed_ids:
                continue
            os_dx = m.min_x - anchor_monitor.min_x
            os_dy = m.min_y - anchor_monitor.min_y
            w = max(1, m.max_x - m.min_x)
            h = max(1, m.max_y - m.min_y)
            derived.append(
                {
                    "client_monitor_id": m.monitor_id,
                    "workspace_x": anchor_wx + os_dx,
                    "workspace_y": anchor_wy + os_dy,
                    "width": w,
                    "height": h,
                }
            )

        return explicit + derived

    def _synthesize_legacy_placement(self, server_monitors) -> list[dict]:
        """Fallback placement for clients that only carry ``screen_position``."""
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
        cw, ch = pw, ph
        cm_id = 0
        if self.monitors:
            cm = next(
                (m for m in self.monitors if m.is_primary),
                self.monitors[0],
            )
            cw = max(1, cm.max_x - cm.min_x)
            ch = max(1, cm.max_y - cm.min_y)
            cm_id = cm.monitor_id

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
        return [
            {
                "client_monitor_id": cm_id,
                "workspace_x": wx,
                "workspace_y": wy,
                "width": cw,
                "height": ch,
            }
        ]

    def get_edge_bindings(self, server_monitors) -> list:
        """Per-placement EdgeBinding list driving cross-screen routing.

        Server side reads ``server_*`` fields, client side reads
        ``client_*``. Local import to keep ``utils.screen`` out of the
        model layer's import graph.
        """
        placements = self.get_effective_placements(server_monitors)
        if not placements:
            return []
        from utils.screen import compute_edge_bindings

        out: list = []
        for p in placements:
            out.extend(compute_edge_bindings(p, server_monitors))
        return out

    def get_intra_client_bindings(self, server_monitors) -> list[dict]:
        """Intra-client warp bindings between abutting placements.

        The client uses these to override OS-level monitor adjacency;
        transitions without a binding get clamped to the source edge.
        """
        placements = self.get_effective_placements(server_monitors)
        if not placements or len(placements) < 2:
            return []
        from utils.screen import compute_intra_client_bindings

        return compute_intra_client_bindings(placements)

    def to_dict(self) -> dict:
        return self.__dict__()

    @staticmethod
    def from_dict(data: dict) -> "ClientObj":
        # Back-compat: legacy "ip_address" (str) vs "ip_addresses" (list).
        ip_addresses = data.get("ip_addresses", None)
        if ip_addresses is None:
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
            # MonitorInfo is a frozen dataclass; JSON/msgpack can't
            # serialise it directly. ``__init__`` re-parses on load.
            "monitors": [m.to_dict() for m in self.monitors],
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
    """Manages multiple ClientObj instances."""

    def __init__(self, client_mode: bool = False):
        # client_mode = True means the manager tracks only a single main client.
        self.clients = []
        self._is_client_main = client_mode

    def update_client(self, client: "ClientObj") -> "ClientsManager":
        """Update existing client info. Matches by uid, then hostname, then IP."""
        for idx, existing_client in enumerate(self.clients):
            if client.uid and existing_client.uid and existing_client.uid == client.uid:
                self.clients[idx] = client
                return self
            if (
                client.host_name
                and existing_client.host_name
                and existing_client.host_name == client.host_name
            ):
                self.clients[idx] = client
                return self
            if any(ip in existing_client.ip_addresses for ip in client.ip_addresses):
                self.clients[idx] = client
                return self
        raise ValueError("Client not found to update.")

    def add_client(self, client: "ClientObj") -> "ClientsManager":
        """Register a client, enforcing identity uniqueness only.

        Spatial placement overlaps are checked separately in
        :meth:`Server.set_client_layout`. Two unplaced clients sharing
        a legacy ``screen_position`` are allowed: routing goes through
        the placement-derived EdgeBinding cache.
        """
        for existing_client in self.clients:
            if client.uid and existing_client.uid and existing_client.uid == client.uid:
                raise ValueError(f"Client with uid '{client.uid}' already exists.")
            if (
                client.host_name
                and existing_client.host_name
                and existing_client.host_name == client.host_name
                and any(
                    ip in existing_client.ip_addresses for ip in client.ip_addresses
                )
            ):
                raise ValueError(
                    f"Client '{client.host_name}' with overlapping IPs already exists."
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
        """Look up a client by UID (preferred), hostname, IP, or screen_position.

        In client mode returns the sole client regardless of filter.
        """
        if self._is_client_main:
            return self.clients[0] if self.clients else None

        for client in self.clients:
            if uid:
                if client.uid == uid:
                    return client
                continue
            if hostname:
                if client.host_name and client.host_name == hostname:
                    return client

            if ip_address:
                if client.has_ip(ip_address):
                    return client
            elif screen_position:
                if client.screen_position == screen_position:
                    return client
        return None
