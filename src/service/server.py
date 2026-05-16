"""
Unified Server API
Provides a clean interface to configure and manage server components.
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

import asyncio
import socket
import sys

from typing import Optional, Dict, Tuple, Callable, Awaitable

from config import ApplicationConfig, ServerConfig
from model.client import ClientObj, ClientsManager
from event.bus import AsyncEventBus
from event.notification import (
    NotificationEvent,
    ClientApprovalRequestedEvent,
    ClientApprovalResolvedEvent,
    ClientConnectedEvent as ClientConnectedNotification,
    ClientDisconnectedEvent as ClientDisconnectedNotification,
    ConfigSavedEvent,
    OtpGeneratedEvent,
    PairingRequestEvent,
    StreamEnabledEvent,
    StreamDisabledEvent,
)
from event import (
    BusEventType,
    ClientConnectedEvent,
    ClientDisconnectedEvent,
    ClientLayoutUpdatedEvent,
    ClientStreamReconnectedEvent,
)
from network.connection.server import ConnectionHandler
from network.stream.handler.server import (
    UnidirectionalStreamHandler,
    BidirectionalStreamHandler,
    MulticastStreamHandler,
)
from network.stream import StreamType
from network.stream.handler import StreamHandler

from command import CommandHandler

from input.cursor import CursorHandlerWorker
from input.mouse import ServerMouseListener, ServerMouseController
from input.keyboard import ServerKeyboardListener
from input.clipboard import ClipboardListener, ClipboardController

from utils.metrics import PerformanceMonitor
from utils.net import get_local_ip
from utils.crypto import CertificateManager
from utils.crypto.sharing import CertificateSharing

from utils.logging import get_logger

from . import ServiceDiscovery


class ServerStartError(Exception):
    """Raised by :meth:`Server.start` when startup fails for a known,
    user-actionable reason. The daemon surfaces ``str(exc)`` directly as the
    CommandError message so the GUI shows a useful description without us
    having to fire a second notification event.
    """

    def __init__(self, message: str, reason: str = "", **details):
        super().__init__(message)
        self.reason = reason
        self.details = details


class Server:
    """
    Manages server configurations, clients, connections, SSL setup, and certificate sharing.

    This class provides features for configuring the server, managing client connections,
    enabling and disabling SSL, managing SSL certificates, sharing certificates securely,
    and maintaining an allowlist of clients. It abstracts away the complexities involved
    in handling connections, certificate generation, and client management.
    """

    CLEANUP_DELAY = 0.5  # seconds to wait during cleanup

    def __init__(
        self,
        app_config: Optional[ApplicationConfig] = None,
        server_config: Optional[ServerConfig] = None,
        auto_load_config: bool = True,
    ):
        """
        Initializes the primary configuration and components of the server application.

        The constructor initializes core configurations, logging, components, and
        registries required for the server application. It loads application-specific
        settings, manages secure connections with optional SSL certificates, and sets
        up core communication components and registries for managing client connections
        and event handling.

        Args:
            app_config: The application-level settings such as directory paths and
                app-specific preferences. Defaults to None, in which case a default
                configuration is initialized.
            server_config: The server's runtime configuration including connection,
                SSL, logging, streams, and authorized clients. Defaults to None, using
                a default configuration.
            auto_load_config: If True, automatically loads configuration from file if exists.
                Defaults to True.

        Attributes:
            app_config (ApplicationConfig): Initialized or passed application
                configuration object.
            config (ServerConfig): Holds all server settings including connection,
                SSL, logging, streams, and authorized clients.
            _cert_manager (CertificateManager): Manages SSL certificates for secure
                connections.
            _cert_sharing (Optional[CertificateSharing]): Facilitates certificate
                sharing between components, initialized only in SSL mode. Defaults to
                None.
            clients_manager (ClientsManager): Handles and tracks currently connected
                clients.
            event_bus (AsyncEventBus): Manages asynchronous events and their
                distribution across the application services.
            _stream_handlers (Dict[int, StreamHandler]): Registry mapping stream
                identifiers to their respective handlers.
            _components (dict): Storage for application components or services
                initialized during runtime by the server.
            _running (bool): Indicates the server's running state. Initialized as
                False.
            connection_handler (Optional[ConnectionHandler]): Coordinates incoming and
                outgoing connections. Defaults to None, and is set during runtime.

        Raises:
            The constructor does not explicitly raise exceptions but may encounter
            errors indirectly if components initialization or configurations fail.
        """
        # Initialize configurations
        self.app_config = app_config or ApplicationConfig()
        self.config = server_config or ServerConfig(self.app_config)

        # Try to load existing configuration if requested
        if auto_load_config:
            self.config.sync_load()

        # Set logging level
        self._logger = get_logger(self.__class__.__name__, level=self.config.log_level)
        self._logger.info(f"Logger initialized at level: {self.config.log_level}")

        # Log loaded clients
        self._load_authorized_clients()

        # Initialize certificate manager
        self._cert_manager = CertificateManager(
            cert_dir=self.app_config.get_certificate_path()
        )
        self._cert_sharing: Optional[CertificateSharing] = None

        # Setup SSL if enabled
        self.certfile, self.keyfile = None, None
        if self.config.ssl_enabled:
            self.certfile, self.keyfile = self._setup_certificates()

        # Initialize event bus
        self.event_bus = AsyncEventBus()

        # Stream handlers registry
        self._stream_handlers: Dict[int, StreamHandler] = {}

        # Components registry
        self._components = {}
        self._running = False

        # Metrics and performance monitoring
        # self._metrics_collector = MetricsCollector()
        # self._performance_monitor = PerformanceMonitor(self._metrics_collector)
        self._metrics_collector = None
        self._performance_monitor = PerformanceMonitor(self._metrics_collector)

        # mDNS
        self._mdns_service = ServiceDiscovery()

        # Connection handler
        self.connection_handler: Optional[ConnectionHandler] = None

        # Notification callback (set by daemon or external controller)
        self._notification_callback: Optional[
            Callable[[NotificationEvent], Awaitable[None]]
        ] = None

        # Pending client-approval requests: each unknown client that initiates
        # a handshake spawns a Future stored here, resolved by the admin via
        # approve_pending_client / deny_pending_client (or by timeout).
        self._pending_approvals: Dict[str, "asyncio.Future[Optional[ClientObj]]"] = {}
        self._pending_approval_meta: Dict[str, Dict[str, str]] = {}
        self._pending_approvals_lock = asyncio.Lock()
        self._approval_request_timeout = 60  # seconds

    @property
    def clients_manager(self) -> ClientsManager:
        """Access the ClientsManager from configuration"""
        return self.config.clients_manager

    # ==================== Notification Callback Management ====================

    def set_notification_callback(
        self, callback: Optional[Callable[[NotificationEvent], Awaitable[None]]]
    ) -> None:
        """
        Set callback for sending notifications about state changes.

        Args:
            callback: Async callback function that receives NotificationEvent
        """
        self._notification_callback = callback

    async def _send_notification(self, event: NotificationEvent) -> None:
        """
        Send notification to registered callback.

        Args:
            event: NotificationEvent to send
        """
        if self._notification_callback:
            try:
                await self._notification_callback(event)
            except Exception as e:
                self._logger.error(f"Error sending notification: {e}")

    # ==================== Configuration Management ====================

    def _load_authorized_clients(self) -> None:
        """Log info about loaded authorized clients (clients are loaded by config)"""
        clients = self.clients_manager.get_clients()
        if len(clients) > 0:
            self._logger.info(
                f"Loaded {len(clients)} authorized clients from configuration"
            )
        else:
            self._logger.info("No authorized clients found in configuration")

    async def save_config(self) -> bool:
        """
        Save current configuration to file.

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Clients are already in config.clients_manager, just save
            await self.config.save()
            self._logger.info("Configuration saved successfully")
            await self._send_notification(ConfigSavedEvent(config_type="server"))
            return True
        except Exception as e:
            self._logger.error(f"Error saving configuration: {e}")
            return False

    async def load_config(self) -> bool:
        """
        Load configuration from file.

        Returns:
            True if loaded successfully, False otherwise
        """
        try:
            if await self.config.load():
                # Clients are already loaded by config
                self._load_authorized_clients()
                self._logger.set_level(self.config.log_level)
                self._logger.info("Configuration loaded successfully")
                return True
            else:
                self._logger.warning("Configuration file not found")
                return False
        except Exception as e:
            self._logger.error(f"Error loading configuration: {e}")
            return False

    # ==================== Certificate Management ====================

    def enable_ssl(self) -> bool:
        """
        Enable SSL for server connections. It will take effect on next start.
        We won't auto save config here, need a manual save after this call.
        """
        try:
            self.certfile, self.keyfile = self._setup_certificates()
        except Exception:
            self.config.disable_ssl()
            self._logger.error("Failed to setup SSL certificates, cannot enable SSL")
            return False

        self.config.enable_ssl()
        self._logger.info("SSL enabled for server connections")
        return True

    def disable_ssl(self) -> None:
        """
        Disable SSL for server connections. It will take effect on next start.
        We won't auto save config here, need a manual save after this call.
        """
        self.certfile = None
        self.keyfile = None
        self.config.disable_ssl()
        self._logger.info("SSL disabled for server connections")

    def _setup_certificates(self) -> Tuple[str, str]:
        """
        Ensure SSL certificates are available.

        Returns:
            Tuple of (certfile_path, keyfile_path)
        """
        try:
            if not self._cert_manager.certificates_exist():
                self._logger.warning(
                    "SSL certificates not found, generating new ones..."
                )
                hostname = socket.gethostname()
                ip = get_local_ip()

                self._cert_manager.generate_ca()
                self._cert_manager.generate_server_certificate(
                    hostname, [ip, "localhost"]
                )

                certfile, keyfile = self._cert_manager.get_server_credentials()
                if not certfile or not keyfile:
                    raise RuntimeError("Failed to generate SSL certificates")

                self._logger.info("SSL certificates generated successfully")
                return certfile, keyfile
            else:
                certfile, keyfile = self._cert_manager.get_server_credentials()
                if not certfile or not keyfile:
                    raise RuntimeError("SSL certificates found but failed to load")

                self._logger.info("SSL certificates found and loaded")
                return certfile, keyfile
        except Exception as e:
            self._logger.error(f"Error setting up SSL certificates ({e})")
            raise

    async def start_pairing_service(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> bool:
        """
        Start the always-on pairing/cert-sharing listener.

        While this listener is running, clients can:

        - send ``REQUEST_PAIRING`` to ask the server to auto-generate an OTP
          and surface it on the admin GUI (no OTP travels over the network);
        - send ``GET_CERTIFICATE`` to download the encrypted CA bundle once an
          OTP is active.

        Idempotent: if the service is already running, this is a no-op.
        """
        if not self._cert_manager.certificates_exist():
            self._logger.warning(
                "No certificates available, skipping pairing service start"
            )
            return False

        if self._cert_sharing and self._cert_sharing.is_sharing_active():
            # Refresh the callback so it captures the current notification
            # callback even if it changed since last start.
            self._cert_sharing.set_pairing_request_callback(self._on_pairing_requested)
            return True

        cert_data = self._cert_manager.load_ca_data()
        if not cert_data:
            self._logger.error("Failed to load CA certificate data")
            return False

        bind_host = host if host is not None else "0.0.0.0"
        # Resolve effective pairing port: explicit arg -> config override ->
        # legacy "port - 2" convention. Keeping the resolution centralised so
        # both server.py and the mDNS TXT record agree.
        bind_port = port if port is not None else self.config.get_pairing_port()
        self._cert_sharing = CertificateSharing(
            cert_data=cert_data,
            host=bind_host,
            port=bind_port,
            timeout=30,
            pairing_request_callback=self._on_pairing_requested,
        )

        ok = await self._cert_sharing.start_service()
        if not ok:
            self._logger.error("Failed to start pairing service")
            self._cert_sharing = None
            return False

        actual = self._cert_sharing.get_actual_port()
        if actual is not None and actual != bind_port:
            self._logger.info(
                f"Pairing service bound to fallback port {actual} "
                f"(preferred {bind_port} was occupied)"
            )
        return True

    def get_pairing_actual_port(self) -> Optional[int]:
        """Return the port the pairing listener actually bound to (with
        fallback applied), or None if not running."""
        if self._cert_sharing is None:
            return None
        return self._cert_sharing.get_actual_port()

    async def _on_pairing_requested(self, info: Dict[str, str]) -> None:
        """Bridge a pairing request from CertificateSharing into notifications.

        Emits two events so the GUI can react with either:
        - the legacy ``OtpGenerated`` path (existing UI just works);
        - the richer ``PairingRequested`` path (shows which client asked).
        """
        try:
            otp = info.get("otp", "")
            timeout_val = int(info.get("timeout", "0") or 0)
            peer_ip = info.get("peer_ip", "")
            hostname = info.get("hostname", "")
            was_active = info.get("was_active", "0") == "1"
        except Exception as e:
            self._logger.error(f"Malformed pairing info payload: {e}")
            return

        await self._send_notification(
            PairingRequestEvent(
                otp=otp,
                timeout=timeout_val,
                peer_ip=peer_ip,
                hostname=hostname,
                was_active=was_active,
            )
        )
        # Mirror as OtpGenerated so existing GUI listeners keep working
        # without needing to know about pairing-vs-manual provenance.
        if otp:
            await self._send_notification(
                OtpGeneratedEvent(otp=otp, timeout=timeout_val)
            )

    async def share_certificate(
        self,
        host: str = "0.0.0.0",
        port: Optional[int] = None,
        timeout: int = 30,
    ) -> Tuple[bool, Optional[str]]:
        """
        Start (or refresh) certificate sharing with an OTP.

        If the always-on pairing service is running, this just ensures an OTP
        is active and returns it - no new socket is opened. Otherwise it falls
        back to the legacy one-shot flow that opens a temporary server.

        Args:
            host: Host address for temporary server (default: all interfaces)
            port: Port for temporary server (default: 55556)
            timeout: OTP validity window in seconds (default: 30)

        Returns:
            Tuple of (success, otp). OTP is None if failed.

        Example:
        ::
            success, otp = await server.share_certificate()
            if success:
                print(f"Share this OTP with client: {otp}")
        """
        if not self._cert_manager.certificates_exist():
            self._logger.error("No certificates available to share")
            return False, None

        bind_port = port if port is not None else self.config.get_pairing_port()

        # Fast path: pairing service already up - just refresh the OTP.
        if self._cert_sharing and self._cert_sharing.is_sharing_active():
            otp, remaining = await self._cert_sharing.ensure_active_otp(timeout=timeout)
            if otp:
                self._logger.info(
                    f"Refreshed OTP via pairing service (valid {remaining}s)"
                )
                return True, otp
            return False, None

        try:
            cert_data = self._cert_manager.load_ca_data()

            if not cert_data:
                self._logger.error("Failed to load CA certificate data")
                return False, None

            self._cert_sharing = CertificateSharing(
                cert_data=cert_data,
                host=host,
                port=bind_port,
                timeout=timeout,
                pairing_request_callback=self._on_pairing_requested,
            )

            success, otp = await self._cert_sharing.start_sharing()

            if success and otp:
                return True, otp
            else:
                self._logger.error("Failed to start certificate sharing")
                return False, None

        except Exception as e:
            self._logger.error(f"Error starting certificate sharing ({e})")
            return False, None

    async def stop_cert_sharing(self):
        """
        Stop certificate sharing server.

        Invalidates OTP and closes temporary server.
        """
        if self._cert_sharing:
            await self._cert_sharing.stop_sharing()
            self._logger.info("Certificate sharing stopped")

    def get_sharing_otp(self) -> Optional[str]:
        """
        Get current OTP for certificate sharing.

        Returns:
            OTP string if valid and sharing is active, None otherwise
        """
        if self._cert_sharing:
            return self._cert_sharing.get_otp()
        return None

    def is_cert_sharing_active(self) -> bool:
        """Check if certificate sharing is currently active"""
        return self._cert_sharing is not None and self._cert_sharing.is_sharing_active()

    # ==================== Client Management ====================

    async def add_client(
        self,
        ip_addresses: Optional[list[str] | str] = None,
        hostname: Optional[str] = None,
        screen_position: str = "top",
        auto_save: bool = True,
    ) -> ClientObj:
        """
        Add a client to the authorized list.

        Args:
            ip_addresses: IP address(es) of the client (single str or list)
            hostname: Hostname of the client
            screen_position: Screen position relative to server
            auto_save: If True, automatically saves configuration after adding

        Returns:
            The created ClientObj
        """
        try:
            client = self.config.add_client(
                ip_addresses=ip_addresses,
                hostname=hostname,
                screen_position=screen_position,
            )

            if auto_save:
                await self.save_config()

            self._logger.info(
                f"Added client {ip_addresses if ip_addresses else hostname} at position {screen_position}"
            )
            return client
        except ValueError as ve:
            self._logger.error(f"Error adding client: {ve}")
            raise

    async def remove_client(
        self,
        ip_address: Optional[str] = None,
        hostname: Optional[str] = None,
        screen_position: Optional[str] = None,
        auto_save: bool = True,
    ) -> bool:
        """
        Remove a client from the authorized list.

        Args:
            ip_address: One of the client's known IP addresses (used for lookup)
            hostname: Hostname of the client
            screen_position: Screen position of the client
            auto_save: If True, automatically saves configuration after removal

        Returns:
            True if client was removed, False if not found
        """
        client = self.config.get_client(
            ip_address=ip_address, hostname=hostname, screen_position=screen_position
        )
        if client:
            if (
                self._running and self.connection_handler is not None
            ):  # If server is running, disconnect client first
                await self.connection_handler.force_disconnect_client(client)
            # Finally remove from allowlist
            self.config.remove_client(client=client)

            if auto_save:
                await self.save_config()

            net_id = ip_address or hostname or screen_position
            self._logger.info(f"Removed client {net_id}")
            return True
        return False

    def get_clients(self) -> list[ClientObj]:
        """Get all registered clients"""
        return self.config.get_clients()

    def get_client(
        self,
        ip_address: Optional[str] = None,
        hostname: Optional[str] = None,
        screen_position: Optional[str] = None,
    ) -> Optional[ClientObj]:
        """Get a specific client"""
        return self.config.get_client(
            ip_address=ip_address, hostname=hostname, screen_position=screen_position
        )

    async def set_client_layout(
        self,
        placements: list[dict],
        uid: Optional[str] = None,
        hostname: Optional[str] = None,
        ip_address: Optional[str] = None,
        auto_save: bool = True,
    ) -> ClientObj:
        """Replace the multi-monitor placements of one client.

        Validates the new list against:

        - basic placement shape (positive width/height, non-negative
          ``client_monitor_id``);
        - no overlap among the client's own placements;
        - no overlap with the server's monitors;
        - no overlap with the placements of OTHER clients (the workspace
          must stay a partition of disjoint rectangles).

        On success the new placements are persisted on the ClientObj
        and (if ``auto_save``) on disk. Subsequent ``CLIENT_CONNECTED``
        events will carry the refreshed :class:`EdgeBinding` list to
        the mouse listener.
        """
        client = self.config.get_client(
            uid=uid,
            ip_address=ip_address,
            hostname=hostname,
        )
        if not client:
            raise ValueError(
                f"Client [uid={uid}, ip={ip_address}, host={hostname}] not found"
            )

        # ------------------------------------------------------------------
        # Validation
        # ------------------------------------------------------------------
        from utils.screen import Screen

        normalized: list[dict] = []
        for raw in placements or []:
            try:
                width = int(raw["width"])
                height = int(raw["height"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"Placement missing width/height: {raw!r}")
            if width <= 0 or height <= 0:
                raise ValueError(
                    f"Placement has non-positive size: {raw!r}"
                )
            normalized.append({
                "client_monitor_id": int(raw.get("client_monitor_id", 0)),
                "workspace_x": int(raw.get("workspace_x", 0)),
                "workspace_y": int(raw.get("workspace_y", 0)),
                "width": width,
                "height": height,
            })

        def _rects_overlap(a: dict, b: dict) -> bool:
            ax2 = a["workspace_x"] + a["width"]
            ay2 = a["workspace_y"] + a["height"]
            bx2 = b["workspace_x"] + b["width"]
            by2 = b["workspace_y"] + b["height"]
            return not (
                ax2 <= b["workspace_x"]
                or bx2 <= a["workspace_x"]
                or ay2 <= b["workspace_y"]
                or by2 <= a["workspace_y"]
            )

        # Self-overlap.
        for i, a in enumerate(normalized):
            for b in normalized[i + 1:]:
                if _rects_overlap(a, b):
                    raise ValueError(
                        f"Placements of {client.get_net_id()} overlap each other: "
                        f"{a} vs {b}"
                    )

        # Overlap with server monitors.
        try:
            server_monitors = Screen.get_monitors()
        except Exception:
            server_monitors = []
        for m in server_monitors:
            sm = {
                "workspace_x": m.min_x,
                "workspace_y": m.min_y,
                "width": m.max_x - m.min_x,
                "height": m.max_y - m.min_y,
            }
            for p in normalized:
                if _rects_overlap(p, sm):
                    raise ValueError(
                        f"Placement {p} overlaps server monitor #{m.monitor_id}"
                    )

        # Overlap with OTHER clients' placements.
        for other in self.config.get_clients():
            if other.get_net_id() == client.get_net_id():
                continue
            for op in other.placements:
                op_norm = {
                    "workspace_x": int(op.get("workspace_x", 0)),
                    "workspace_y": int(op.get("workspace_y", 0)),
                    "width": int(op.get("width", 0)),
                    "height": int(op.get("height", 0)),
                }
                if op_norm["width"] <= 0 or op_norm["height"] <= 0:
                    continue
                for p in normalized:
                    if _rects_overlap(p, op_norm):
                        raise ValueError(
                            f"Placement {p} overlaps {other.get_net_id()}'s "
                            f"placement {op_norm}"
                        )

        # Commit.
        client.placements = normalized
        self.clients_manager.update_client(client)
        if auto_save:
            await self.save_config()

        # Hot-reload the mouse listener's routing cache so the new
        # placements take effect on the very next mouse crossing,
        # without requiring the client to reconnect.
        edge_bindings = [
            eb.to_dict() for eb in client.get_edge_bindings(server_monitors)
        ]
        await self.event_bus.dispatch(
            event_type=BusEventType.CLIENT_LAYOUT_UPDATED,
            data=ClientLayoutUpdatedEvent(
                client_screen=client.get_screen_position(),
                edge_bindings=edge_bindings,
            ),
        )

        self._logger.info(
            f"Set layout for client {client.get_net_id()}: "
            f"{len(normalized)} placement(s)"
        )
        return client

    async def edit_client(
        self,
        ip_address: Optional[str] = None,
        hostname: Optional[str] = None,
        old_screen_position: Optional[str] = None,
        new_screen_position: Optional[str] = None,
        new_ip_addresses: Optional[list[str] | str] = None,
        auto_save: bool = True,
    ) -> ClientObj:
        """
        Edit a client's properties.

        Args:
            ip_address: One of the client's known IP addresses (used for lookup)
            hostname: Hostname of the client to edit
            old_screen_position: Current screen position (used for lookup)
            new_screen_position: New screen position
            new_ip_addresses: New IP address(es) to set (replaces existing list)
            auto_save: If True, automatically saves configuration after editing

        Returns:
            The updated ClientObj
        """
        client = self.config.get_client(
            ip_address=ip_address,
            hostname=hostname,
            screen_position=old_screen_position,
        )
        if not client:
            raise ValueError(f"Client [IP {ip_address}, Host {hostname}] not found")

        # if client is connected do not allow changing screen_position
        if client.is_connected:
            raise RuntimeError("Cannot edit a connected client's properties")

        if new_screen_position:
            client.screen_position = new_screen_position

        if new_ip_addresses is not None:
            if isinstance(new_ip_addresses, str):
                new_ip_addresses = [new_ip_addresses]
            client.ip_addresses = new_ip_addresses

        self.clients_manager.update_client(client)

        if auto_save:
            await self.save_config()

        net_id = ip_address or hostname
        self._logger.info(
            f"Edited client {net_id}: screen_position={new_screen_position}"
        )
        return client

    def is_client_alive(
        self, ip_address: Optional[str] = None, hostname: Optional[str] = None
    ) -> bool:
        """Check if a client is currently connected"""
        if not ip_address and not hostname:
            raise ValueError("Either ip_address or hostname must be provided")

        client = self.config.get_client(ip_address=ip_address, hostname=hostname)
        return client.is_connected if client else False

    async def clear_clients(self, auto_save: bool = True) -> None:
        """
        Remove all clients from authorized list.

        Args:
            auto_save: If True, automatically saves configuration after clearing
        """
        self.config.clients_manager.clear()

        if auto_save:
            await self.save_config()

        self._logger.info("Cleared all clients")

    # ==================== Stream Management ====================

    async def enable_stream(self, stream_type: int) -> None:
        """
        Enable a specific stream type (applies before start or at runtime)

        Raises:
            ValueError: If the stream type is invalid
        """
        if not StreamType.is_valid(stream_type):
            raise ValueError(f"Invalid stream type: {stream_type}")

        self.config.enable_stream(stream_type)
        await self.config.save()
        self._logger.info(f"Enabled stream: {stream_type}")

        await self._send_notification(StreamEnabledEvent(stream_type=stream_type))

    async def disable_stream(self, stream_type: int) -> None:
        """
        Disable a specific stream type (applies before start or at runtime)

        Raises:
            ValueError: If the stream type is invalid
        """
        if not StreamType.is_valid(stream_type):
            raise ValueError(f"Invalid stream type: {stream_type}")

        if StreamType.COMMAND == stream_type:
            self._logger.warning(
                "Command stream is always enabled and cannot be disabled"
            )
            return
        self.config.disable_stream(stream_type)
        await self.config.save()
        self._logger.info(f"Disabled stream: {stream_type}")

        await self._send_notification(StreamDisabledEvent(stream_type=stream_type))

    def is_stream_enabled(self, stream_type: int) -> bool:
        """Check if a stream is enabled"""
        return self.config.is_stream_enabled(stream_type)

    async def enable_stream_runtime(self, stream_type: int) -> bool:
        """Enable a stream at runtime"""
        if not self._running:
            await self.enable_stream(stream_type)
            return True

        # Se già abilitato, non fare nulla
        if self.is_stream_enabled(stream_type):
            self._logger.warning(f"Stream {stream_type} already enabled")
            return True

        # Abilita nella configurazione
        await self.enable_stream(stream_type)

        # Inizializza e avvia i componenti per questo stream
        try:
            if stream_type == StreamType.MOUSE:
                await self._enable_mouse_stream()
            elif stream_type == StreamType.KEYBOARD:
                await self._enable_keyboard_stream()
            elif stream_type == StreamType.CLIPBOARD:
                await self._enable_clipboard_stream()
            else:
                self._logger.error(f"Unknown stream type: {stream_type}")
                return False

            self._logger.info(f"Runtime enabled stream: {stream_type}")
            return True
        except Exception as e:
            import traceback

            traceback.print_exc()
            await self.disable_stream(stream_type)
            self._logger.error(f"Failed to enable {stream_type} stream ({e})")
            raise RuntimeError(f"Failed to enable {stream_type} stream ({e})")

    async def disable_stream_runtime(self, stream_type: int) -> bool:
        """Disable a stream at runtime"""
        if not self._running or not self.is_stream_enabled(stream_type):
            await self.disable_stream(stream_type)
            return True

        # Disabilita nella configurazione
        await self.disable_stream(stream_type)

        # Ferma e rimuovi i componenti
        try:
            if stream_type == StreamType.MOUSE:
                await self._disable_mouse_stream()
            elif stream_type == StreamType.KEYBOARD:
                await self._disable_keyboard_stream()
            elif stream_type == StreamType.CLIPBOARD:
                await self._disable_clipboard_stream()
            else:
                self._logger.error(f"Unknown stream type: {stream_type}")
                return False

            self._logger.info(f"Runtime disabled stream: {stream_type}")
            return True
        except Exception as e:
            self._logger.error(f"Failed to disable {stream_type} stream ({e})")
            raise RuntimeError(f"Failed to disable {stream_type} stream ({e})")

    # ==================== Client Approval (interactive) ====================

    async def _request_client_approval(
        self, peer_ip: str, hostname: str, uid: str
    ) -> Optional[ClientObj]:
        """Ask the admin (via GUI) whether to accept an unknown client.

        Called by the ConnectionHandler when a client not present in the
        allowlist completes the handshake. Emits a notification with the
        client's identifiers, then blocks on a Future that the admin resolves
        via :meth:`approve_pending_client` or :meth:`deny_pending_client`.

        Returns:
            A populated ClientObj if approved (already added to the
            allowlist), or None if denied / timed out.
        """
        request_id = f"{peer_ip}-{int(asyncio.get_running_loop().time() * 1000)}"

        async with self._pending_approvals_lock:
            existing = self._pending_approvals.get(peer_ip)
            if existing is not None and not existing.done():
                # A second handshake attempt from the same IP while an admin
                # decision is pending: piggy-back on the same Future instead
                # of stacking prompts.
                self._logger.info(
                    f"Reusing pending approval for {peer_ip} (hostname={hostname})"
                )
                fut = existing
            else:
                fut = asyncio.get_running_loop().create_future()
                self._pending_approvals[peer_ip] = fut
                self._pending_approval_meta[peer_ip] = {
                    "hostname": hostname,
                    "uid": uid,
                    "request_id": request_id,
                }

        if existing is None or existing.done():
            await self._send_notification(
                ClientApprovalRequestedEvent(
                    peer_ip=peer_ip,
                    hostname=hostname,
                    uid=uid,
                    request_id=request_id,
                    timeout=self._approval_request_timeout,
                )
            )

        try:
            result = await asyncio.wait_for(
                asyncio.shield(fut), timeout=self._approval_request_timeout
            )
            return result
        except asyncio.TimeoutError:
            self._logger.warning(
                f"Approval request for {peer_ip} timed out - denying by default"
            )
            await self._resolve_pending_approval(peer_ip, None, reason="timeout")
            return None
        finally:
            # ``_resolve_pending_approval`` already
            # pops the entries, but if the waiter was cancelled mid-flight
            # the resolver may never run. Unconditional pop here closes that
            # leak path.
            async with self._pending_approvals_lock:
                self._pending_approvals.pop(peer_ip, None)
                self._pending_approval_meta.pop(peer_ip, None)

    async def _resolve_pending_approval(
        self,
        peer_ip: str,
        client: Optional[ClientObj],
        screen_position: str = "",
        reason: str = "",
    ) -> bool:
        """Resolve a pending approval Future. Returns True if a Future existed.

        Pops the entry from both ``_pending_approvals`` and
        ``_pending_approval_meta`` under the lock so a cancelled waiter can't
        leave stale state behind.
        """
        async with self._pending_approvals_lock:
            fut = self._pending_approvals.pop(peer_ip, None)
            meta = self._pending_approval_meta.pop(peer_ip, None) or {}
        if fut is None or fut.done():
            return False
        fut.set_result(client)
        await self._send_notification(
            ClientApprovalResolvedEvent(
                peer_ip=peer_ip,
                approved=client is not None,
                request_id=meta.get("request_id", ""),
                screen_position=screen_position,
                reason=reason,
            )
        )
        return True

    async def approve_pending_client(
        self, peer_ip: str, screen_position: str = "top"
    ) -> bool:
        """Approve an unknown client that's waiting for the admin's OK.

        Adds the client to the persistent allowlist with the chosen screen
        position before unblocking the handshake.
        """
        async with self._pending_approvals_lock:
            meta = self._pending_approval_meta.get(peer_ip)
            fut = self._pending_approvals.get(peer_ip)
        if fut is None or fut.done():
            self._logger.warning(
                f"No pending approval for {peer_ip} (or already resolved)"
            )
            return False

        hostname = (meta or {}).get("hostname") or None
        try:
            client = await self.add_client(
                ip_addresses=peer_ip,
                hostname=hostname,
                screen_position=screen_position,
                auto_save=True,
            )
        except Exception as e:
            self._logger.error(f"Failed to add approved client {peer_ip} ({e})")
            await self._resolve_pending_approval(
                peer_ip, None, reason=f"add_failed: {e}"
            )
            return False

        await self._resolve_pending_approval(
            peer_ip, client, screen_position=screen_position, reason="approved"
        )
        return True

    async def deny_pending_client(self, peer_ip: str, reason: str = "denied") -> bool:
        """Deny an unknown client awaiting approval."""
        return await self._resolve_pending_approval(peer_ip, None, reason=reason)

    def get_pending_approvals(self) -> list[Dict[str, str]]:
        """Snapshot of currently pending approvals (for status queries)."""
        return [
            {"peer_ip": ip, **(self._pending_approval_meta.get(ip, {}))}
            for ip, fut in self._pending_approvals.items()
            if not fut.done()
        ]

    @staticmethod
    def _is_port_available(host: str, port: int) -> bool:
        """Synchronously probe whether ``host:port`` is free for bind.

        Mirrors what ``asyncio.start_server`` would actually attempt: on POSIX
        the event loop sets ``SO_REUSEADDR`` by default, so sockets stuck in
        ``TIME_WAIT`` from a previous incarnation don't block a fresh bind.
        Without ``SO_REUSEADDR`` here this probe was reporting "port busy"
        for ~60s after every stop, even though the real start would have
        succeeded fine.

        On Windows ``SO_REUSEADDR`` has different (looser) semantics; setting
        it would let two unrelated servers steal each other's port, so we
        keep the probe strict there to match the OS behaviour.
        """
        bind_host = host if host and host != "0.0.0.0" else ""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if sys.platform != "win32":
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((bind_host, port))
            return True
        except OSError:
            return False

    # ==================== Server Lifecycle ====================

    async def start(self) -> bool:
        """Start the server with enabled components"""
        if self._running:
            self._logger.warning("Server already running")
            return False

        self._logger.info("Starting Server...")

        # Pre-flight check: refuse to start if the configured TCP data port
        # is already taken. Unlike the pairing port (which auto-falls-back
        # to adjacent ports), the data port is published over mDNS and used
        # by client configs, so a silent fallback would confuse everything.
        # Surface the conflict explicitly so the GUI can prompt the admin to
        # change the port in Options.
        if not self._is_port_available(self.config.host, self.config.port):
            error_msg = (
                f"Port {self.config.port} is already in use on "
                f"{self.config.host or 'all interfaces'}. "
                f"Change the port in Options and try again."
            )
            self._logger.error(error_msg)
            raise ServerStartError(
                error_msg,
                reason="port_in_use",
                port=self.config.port,
                host=self.config.host,
            )

        # Initialize and start enabled streams
        try:
            await self._initialize_streams()
        except Exception as e:
            self._logger.error(f"Failed to initialize streams ({e})")
            await self.stop(True)
            return False

        # Initialize connection handler
        self.connection_handler = ConnectionHandler(
            connected_callback=self._on_client_connected,
            disconnected_callback=self._on_client_disconnected,
            reconnected_callback=self._on_client_stream_reconnected,
            host=self.config.host,
            port=self.config.port,
            heartbeat_interval=self.config.heartbeat_interval,
            allowlist=self.clients_manager,
            certfile=self.certfile,
            keyfile=self.keyfile,
            approval_callback=self._request_client_approval,
            server_uid=self.config.uid,
        )

        # Initialize and start enabled components
        try:
            await self._initialize_components()
        except Exception as e:
            self._logger.error(f"Failed to initialize components ({e})")
            await self.stop(True)
            return False

        if not await self.connection_handler.start():
            self._logger.error("Failed to start connection handler")
            await self.stop(True)
            return False

        # Bring up the pairing/cert-sharing listener BEFORE mDNS so the
        # service record advertises the port we actually bound (the listener
        # may have fallen back to an adjacent port if the preferred one was
        # busy). Failure is non-fatal - the rest of the server keeps running
        # and the admin can still share certs manually via
        # share_certificate().
        if self.config.ssl_enabled:
            try:
                await self.start_pairing_service(host=self.config.host)
            except Exception as e:
                self._logger.warning(f"Pairing service did not start ({e})")

        # Start mDNS service. Advertise the pairing port so clients can
        # discover it without relying on the legacy ``port - 2`` convention.
        # Prefer the actually-bound port over the configured one so the
        # advertisement reflects reality after fallback.
        actual_pairing = self.get_pairing_actual_port()
        advertised_pairing = (
            actual_pairing if actual_pairing else self.config.get_pairing_port()
        )
        extra_props = {"pairing_port": str(advertised_pairing)}
        try:
            service_task = asyncio.create_task(
                self._mdns_service.register_service(
                    host=self.config.host,
                    port=self.config.port,
                    uid=self.config.uid,
                    extra_props=extra_props,
                )
            )
        except RuntimeError as re:
            self._logger.warning(f"Failed to start mDNS service ({re})")
            # TODO: Should we stop on fail? mDNS is not critical

        try:
            await service_task
            if (
                self.config.uid is None
            ):  # At this point we have a UID generated and assigned
                self.config.uid = self._mdns_service.get_uid()
                await self.save_config()
            # Backfill the handshake-ack UID now that we know it. First-run
            # servers build the connection handler before mDNS assigns a
            # UID, so without this clients would see ``server_uid=""``.
            if self.connection_handler is not None:
                self.connection_handler.set_server_uid(self.config.uid)
        except RuntimeError as re:
            self._logger.warning(f"Failed to start mDNS service ({re})")
        except Exception as e:
            self._logger.error(f"Failed to start mDNS service ({e})")
            await self.stop(True)
            return False

        self._running = True
        self._logger.info(f"Server started on {self.config.host}:{self.config.port}")
        return True

    async def stop(self, force: bool = False):
        """Stop all server components"""
        if not self._running and not force:
            self._logger.warning("Server not running")
            return

        self._logger.info("Stopping Server...")

        tasks: list[asyncio.Task] = []

        # Cancel any pending client-approval prompts so admin GUI stops
        # showing them and the futures don't leak.
        for ip, fut in list(self._pending_approvals.items()):
            if not fut.done():
                fut.set_result(None)
            self._pending_approvals.pop(ip, None)
            self._pending_approval_meta.pop(ip, None)

        # Stop connection handler
        if self.connection_handler:
            await self.connection_handler.stop()

        # Stop pairing/cert-sharing listener
        if self._cert_sharing:
            try:
                await self._cert_sharing.stop_sharing()
            except Exception as e:
                self._logger.warning(f"Error stopping pairing service ({e})")
            self._cert_sharing = None

        # Stop all components
        for component_name, component in list(self._components.items()):
            try:
                if hasattr(component, "stop"):
                    if asyncio.iscoroutinefunction(component.stop):
                        tasks.append(asyncio.create_task(component.stop()))
                    else:
                        component.stop()
            except Exception as e:
                self._logger.error(f"Error stopping component {component_name} ({e})")

        # Stop all stream handlers
        for stream_type, handler in list(self._stream_handlers.items()):
            try:
                if hasattr(handler, "stop"):
                    tasks.append(asyncio.create_task(handler.stop()))
            except Exception as e:
                self._logger.error(f"Error stopping stream handler {stream_type} ({e})")

        # Stop performance monitor
        tasks.append(asyncio.create_task(self._performance_monitor.stop()))

        # mDNS service unregister
        tasks.append(asyncio.create_task(self._mdns_service.unregister_service()))

        # Wait a moment for cleanup
        await asyncio.sleep(self.CLEANUP_DELAY)

        # Await all stop tasks
        if len(tasks) > 0:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                self._logger.error(f"Error during shutdown tasks ({e})")

        self.cleanup()
        self._running = False
        self._logger.info("Server stopped")

    def cleanup(self):
        """Cleanup client resources"""
        self._logger.info("Cleaning up resources...")
        # We cleanup component and stream handlers obj from memory
        self._components.clear()
        self._stream_handlers.clear()

        # We also reset event bus
        self.event_bus = AsyncEventBus()

        self._logger.info("Resources cleaned up.")

    def is_running(self) -> bool:
        """Check if server is running"""
        return self._running

    # ==================== Private Initialization Methods ====================

    async def _initialize_streams(self):
        """Initialize stream handlers and start enabled streams"""
        #! We need to create all stream handlers first to allow event listening and state sync

        # Command stream (always required)
        self._stream_handlers[StreamType.COMMAND] = BidirectionalStreamHandler(
            stream_type=StreamType.COMMAND,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ServerCommandStreamHandler",
            metrics_collector=self._metrics_collector,
            do_cleanup=False,  # Command stream should not cleanup on state changes
        )
        # Force start command stream
        if not await self._stream_handlers[StreamType.COMMAND].start():
            raise RuntimeError("Failed to start command stream handler")
        # Add to enabled streams if not present
        if not self.is_stream_enabled(StreamType.COMMAND):
            await self.enable_stream(StreamType.COMMAND)

        # Mouse stream
        self._stream_handlers[StreamType.MOUSE] = UnidirectionalStreamHandler(
            stream_type=StreamType.MOUSE,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ServerMouseStreamHandler",
            sender=True,
            metrics_collector=self._metrics_collector,
            buffer_size=10000,
        )

        # Keyboard stream
        self._stream_handlers[StreamType.KEYBOARD] = UnidirectionalStreamHandler(
            stream_type=StreamType.KEYBOARD,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ServerKeyboardStreamHandler",
            sender=True,
            metrics_collector=self._metrics_collector,
        )

        # Clipboard stream
        self._stream_handlers[StreamType.CLIPBOARD] = MulticastStreamHandler(
            stream_type=StreamType.CLIPBOARD,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ServerClipboardStreamHandler",
            metrics_collector=self._metrics_collector,
        )

        # Start all stream handlers
        for stream_type, handler in self._stream_handlers.items():
            if self.is_stream_enabled(stream_type):
                if not await handler.start():
                    raise RuntimeError(f"Failed to start stream handler: {stream_type}")

    async def _initialize_components(self):
        """Initialize enabled components based on configuration"""
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Command handler (always required)
        self._components["command_handler"] = CommandHandler(
            event_bus=self.event_bus, stream=command_stream
        )

        # Initialize all stream components (they will start only if enabled)
        await self._enable_mouse_stream()
        await self._enable_keyboard_stream()
        await self._enable_clipboard_stream()

    # ==================== Runtime Enable/Disable Methods ====================

    async def _ensure_stream_active(self, stream_type: int, stream_handler) -> None:
        """Helper: Ensure stream handler is started if enabled"""
        is_enabled = self.is_stream_enabled(stream_type)
        if is_enabled and not stream_handler.is_active():
            if not await stream_handler.start():
                raise RuntimeError(f"Failed to start stream handler for {stream_type}")

    async def _enable_mouse_stream(self):
        """Enable mouse stream and components at runtime"""
        # Get or create stream handler
        mouse_stream = self._stream_handlers.get(StreamType.MOUSE)
        if not mouse_stream:
            mouse_stream = UnidirectionalStreamHandler(
                stream_type=StreamType.MOUSE,
                clients=self.clients_manager,
                event_bus=self.event_bus,
                handler_id="ServerMouseStreamHandler",
                sender=True,
                buffer_size=10000,  # Higher needed for high polling rates
            )
            self._stream_handlers[StreamType.MOUSE] = mouse_stream

        # Start stream if enabled
        await self._ensure_stream_active(StreamType.MOUSE, mouse_stream)

        is_enabled = self.is_stream_enabled(StreamType.MOUSE)
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Cursor Handler - manages cursor visibility
        cursor_handler = self._components.get("cursor_handler")
        if not cursor_handler:
            cursor_handler = CursorHandlerWorker(
                event_bus=self.event_bus, stream=mouse_stream, debug=False
            )
            if is_enabled and not await cursor_handler.start():
                raise RuntimeError("Failed to start cursor handler")
            self._components["cursor_handler"] = cursor_handler
        elif is_enabled and not cursor_handler.is_alive():
            if not await cursor_handler.start():
                await self._disable_mouse_stream()
                raise RuntimeError("Failed to start cursor handler")

        # Mouse Controller - handles incoming mouse commands
        if not self._components.get("mouse_controller"):
            self._components["mouse_controller"] = ServerMouseController(
                event_bus=self.event_bus
            )

        # Mouse Listener - captures and sends mouse events
        mouse_listener = self._components.get("mouse_listener")
        if not mouse_listener:
            mouse_listener = ServerMouseListener(
                event_bus=self.event_bus,
                stream_handler=mouse_stream,
                command_stream=command_stream,
                filtering=False,
            )
            if is_enabled and not mouse_listener.start():
                await self._disable_mouse_stream()
                raise RuntimeError("Failed to start mouse listener")
            self._components["mouse_listener"] = mouse_listener
        elif is_enabled and not mouse_listener.is_alive():
            if not mouse_listener.start():
                await self._disable_mouse_stream()
                raise RuntimeError("Failed to start mouse listener")

    async def _disable_mouse_stream(self):
        """Disable mouse stream and components at runtime"""
        # Stop mouse listener
        mouse_listener = self._components.get("mouse_listener")
        if mouse_listener:
            mouse_listener.stop()

        # Stop stream handler
        mouse_stream = self._stream_handlers.get(StreamType.MOUSE)
        if mouse_stream:
            await mouse_stream.stop()

    async def _enable_keyboard_stream(self):
        """Enable keyboard stream and components at runtime"""
        # Get or create stream handler
        keyboard_stream = self._stream_handlers.get(StreamType.KEYBOARD)
        if not keyboard_stream:
            keyboard_stream = UnidirectionalStreamHandler(
                stream_type=StreamType.KEYBOARD,
                clients=self.clients_manager,
                event_bus=self.event_bus,
                handler_id="ServerKeyboardStreamHandler",
                sender=True,
            )
            self._stream_handlers[StreamType.KEYBOARD] = keyboard_stream

        # Start stream if enabled
        await self._ensure_stream_active(StreamType.KEYBOARD, keyboard_stream)

        is_enabled = self.is_stream_enabled(StreamType.KEYBOARD)
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Keyboard Listener - captures and sends keyboard events
        keyboard_listener = self._components.get("keyboard_listener")
        if not keyboard_listener:
            keyboard_listener = ServerKeyboardListener(
                event_bus=self.event_bus,
                stream_handler=keyboard_stream,
                command_stream=command_stream,
            )
            if is_enabled and not keyboard_listener.start():
                raise RuntimeError("Failed to start keyboard listener")
            self._components["keyboard_listener"] = keyboard_listener
        elif is_enabled and not keyboard_listener.is_alive():
            if not keyboard_listener.start():
                await self._disable_keyboard_stream()
                raise RuntimeError("Failed to start keyboard listener")

    async def _disable_keyboard_stream(self):
        """Disable keyboard stream and components at runtime"""
        # Stop keyboard listener
        keyboard_listener = self._components.get("keyboard_listener")
        if keyboard_listener:
            keyboard_listener.stop()

        # Stop stream handler
        keyboard_stream = self._stream_handlers.get(StreamType.KEYBOARD)
        if keyboard_stream:
            await keyboard_stream.stop()

    async def _enable_clipboard_stream(self):
        """Enable clipboard stream and components at runtime"""
        # Get or create stream handler
        clipboard_stream = self._stream_handlers.get(StreamType.CLIPBOARD)
        if not clipboard_stream:
            clipboard_stream = MulticastStreamHandler(
                stream_type=StreamType.CLIPBOARD,
                clients=self.clients_manager,
                event_bus=self.event_bus,
                handler_id="ServerClipboardStreamHandler",
            )
            self._stream_handlers[StreamType.CLIPBOARD] = clipboard_stream

        # Start stream if enabled
        await self._ensure_stream_active(StreamType.CLIPBOARD, clipboard_stream)

        is_enabled = self.is_stream_enabled(StreamType.CLIPBOARD)
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Clipboard Listener - monitors clipboard changes
        clipboard_listener = self._components.get("clipboard_listener")
        if not clipboard_listener:
            clipboard_listener = ClipboardListener(
                event_bus=self.event_bus,
                stream_handler=clipboard_stream,
                command_stream=command_stream,
            )
            if is_enabled and not await clipboard_listener.start():
                raise RuntimeError("Failed to start clipboard listener")
            self._components["clipboard_listener"] = clipboard_listener
        elif is_enabled and not clipboard_listener.is_alive():
            if not await clipboard_listener.start():
                await self._disable_clipboard_stream()
                raise RuntimeError("Failed to start clipboard listener")

        # Clipboard Controller - handles incoming clipboard updates
        if not self._components.get("clipboard_controller"):
            clipboard_controller = ClipboardController(
                event_bus=self.event_bus,
                clipboard=self._components[
                    "clipboard_listener"
                ].get_clipboard_context(),
                stream_handler=clipboard_stream,
            )

            self._components["clipboard_controller"] = clipboard_controller

    async def _disable_clipboard_stream(self):
        """Disable clipboard stream and components at runtime"""
        # Stop clipboard listener
        clipboard_listener = self._components.get("clipboard_listener")
        if clipboard_listener:
            await clipboard_listener.stop()

        # Stop stream handler
        clipboard_stream = self._stream_handlers.get(StreamType.CLIPBOARD)
        if clipboard_stream:
            await clipboard_stream.stop()

    # ==================== Event Callbacks ====================

    async def _on_client_connected(self, client: ClientObj, streams: list[int]):
        """Handle client connection event"""
        # Derive the spatial cross-screen contract from the client's
        # placements + this server's monitor list. The mouse listener
        # uses these to route ``(server_monitor, edge, axis_norm)``
        # crossings to the right client monitor. Empty when the layout
        # editor hasn't been used yet — routing then falls back to the
        # legacy ``screen_position`` enum.
        try:
            from utils.screen import Screen

            server_monitors = Screen.get_monitors()
        except Exception:
            server_monitors = []
        edge_bindings = [
            eb.to_dict() for eb in client.get_edge_bindings(server_monitors)
        ]

        await self.event_bus.dispatch(
            event_type=BusEventType.CLIENT_CONNECTED,
            data=ClientConnectedEvent(
                client_screen=client.get_screen_position(),
                streams=streams,
                edge_bindings=edge_bindings,
            ),
        )
        # Save config on new connection
        await self.save_config()
        self._logger.info(
            f"Client {client.get_net_id()} connected at position {client.screen_position}"
        )

        # Send notification
        await self._send_notification(
            ClientConnectedNotification(
                client=client.to_dict(),
            )
        )

    async def _on_client_disconnected(self, client: ClientObj, streams: list[int]):
        """Handle client disconnection event"""
        await self.event_bus.dispatch(
            event_type=BusEventType.CLIENT_DISCONNECTED,
            data=ClientDisconnectedEvent(
                client_screen=client.get_screen_position(), streams=streams
            ),
        )
        await self.save_config()
        self._logger.info(
            f"Client {client.get_net_id()} disconnected from position {client.screen_position}"
        )

        # Send notification
        await self._send_notification(
            ClientDisconnectedNotification(
                client=client.to_dict(),
            )
        )

    async def _on_client_stream_reconnected(
        self, client: ClientObj, streams: list[int]
    ):
        """Handle client stream reconnection event"""
        await self.event_bus.dispatch(
            event_type=BusEventType.CLIENT_STREAM_RECONNECTED,
            data=ClientStreamReconnectedEvent(
                client_screen=client.get_screen_position(), streams=streams
            ),
        )

    # ==================== Utility Methods ====================

    def get_event_bus(self) -> AsyncEventBus:
        """Get the event bus instance"""
        return self.event_bus

    def get_stream_handler(self, stream_type: int) -> Optional[object]:
        """Get a specific stream handler"""
        return self._stream_handlers.get(stream_type)

    def get_component(self, component_name: str) -> Optional[object]:
        """Get a specific component by name"""
        return self._components.get(component_name)

    def get_enabled_streams(self, parse: bool = False) -> list[int] | dict[int, bool]:
        """Get list of enabled stream types"""
        if parse:
            return [k for k, v in self.config.streams_enabled.items() if v]
        return self.config.streams_enabled

    def get_active_streams(self) -> list[int]:
        """Get list of currently active stream types"""
        return list(self._stream_handlers.keys())

    async def start_metrics_collection(self):
        """Start metrics collection"""
        await self._performance_monitor.start()

    async def stop_metrics_collection(self):
        """Stop metrics collection"""
        await self._performance_monitor.stop()
