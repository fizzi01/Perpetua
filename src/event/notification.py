"""
Notification events for daemon-to-client communication.

This module defines notification event types and a notification manager
for sending state change events from the daemon to connected clients (GUI).
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Callable, Awaitable
import json


class NotificationEventType(str, Enum):
    """Types of notification events sent from daemon to client"""

    # Service lifecycle events
    SERVICE_INITIALIZED = "service_initialized"
    SERVICE_STARTING = "service_starting"
    SERVICE_STARTED = "service_started"
    SERVICE_STOPPING = "service_stopping"
    SERVICE_STOPPED = "service_stopped"
    SERVICE_ERROR = "service_error"

    # Connection events
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECTION_ERROR = "connection_error"
    CONNECTION_LOST = "connection_lost"
    RECONNECTING = "reconnecting"
    RECONNECTED = "reconnected"

    # Server discovery events (client)
    DISCOVERY_STARTED = "discovery_started"
    SERVER_LIST_FOUND = "server_list_found"
    SERVER_DISCOVERED = "server_discovered"
    DISCOVERY_COMPLETED = "discovery_completed"
    DISCOVERY_TIMEOUT = "discovery_timeout"

    # Authentication events
    OTP_NEEDED = "otp_needed"
    OTP_VALIDATED = "otp_validated"
    OTP_INVALID = "otp_invalid"
    OTP_GENERATED = "otp_generated"
    SSL_HANDSHAKE_STARTED = "ssl_handshake_started"
    SSL_HANDSHAKE_COMPLETED = "ssl_handshake_completed"
    SSL_HANDSHAKE_FAILED = "ssl_handshake_failed"
    CERTIFICATE_SHARED = "certificate_shared"
    CERTIFICATE_RECEIVED = "certificate_received"

    # Server choice events (client)
    SERVER_CHOICE_NEEDED = "server_choice_needed"
    SERVER_CHOICE_MADE = "server_choice_made"

    # Client management events (server)
    CLIENT_CONNECTED = "client_connected"
    CLIENT_DISCONNECTED = "client_disconnected"
    CLIENT_AUTHENTICATED = "client_authenticated"
    CLIENT_ADDED = "client_added"
    CLIENT_REMOVED = "client_removed"
    CLIENT_UPDATED = "client_updated"

    # Stream events
    STREAM_ENABLED = "stream_enabled"
    STREAM_DISABLED = "stream_disabled"
    STREAMS_UPDATED = "streams_updated"

    # Configuration events
    CONFIG_LOADED = "config_loaded"
    CONFIG_SAVED = "config_saved"
    CONFIG_UPDATED = "config_updated"
    CONFIG_ERROR = "config_error"

    # State change events
    STATE_CHANGED = "state_changed"
    MODE_CHANGED = "mode_changed"

    # Screen events
    SCREEN_CHANGED = "screen_changed"
    SCREEN_TRANSITION_STARTED = "screen_transition_started"
    SCREEN_TRANSITION_COMPLETED = "screen_transition_completed"

    # Transfer events
    FILE_TRANSFER_STARTED = "file_transfer_started"
    FILE_TRANSFER_PROGRESS = "file_transfer_progress"
    FILE_TRANSFER_COMPLETED = "file_transfer_completed"
    FILE_TRANSFER_FAILED = "file_transfer_failed"
    CLIPBOARD_SYNCED = "clipboard_synced"

    # Network events
    NETWORK_LATENCY_HIGH = "network_latency_high"
    NETWORK_QUALITY_DEGRADED = "network_quality_degraded"
    NETWORK_QUALITY_RESTORED = "network_quality_restored"

    # General events
    STATUS_UPDATE = "status_update"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    TEST = "test"

    # Command result events
    COMMAND_SUCCESS = "command_success"
    COMMAND_ERROR = "command_error"

    PONG = "pong"


@dataclass
class NotificationEvent:
    """
    A notification event sent from daemon to client.

    Attributes:
        event_type: Type of notification event
        data: Event-specific data
        timestamp: When the event occurred
        source: Source service that generated the event (e.g., 'client', 'server', 'daemon')
        message: Optional human-readable message
        metadata: Additional metadata about the event
    """

    event_type: NotificationEventType
    data: Optional[Dict[str, Any]] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    source: str = "daemon"
    message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary"""
        result = {
            "event_type": self.event_type.value
            if isinstance(self.event_type, Enum)
            else self.event_type,
            "timestamp": self.timestamp,
            "source": self.source,
        }
        if self.data is not None:
            result["data"] = self.data
        if self.message is not None:
            result["message"] = self.message
        if self.metadata is not None:
            result["metadata"] = self.metadata
        return result

    def to_json(self) -> str:
        """Convert event to JSON string"""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NotificationEvent":
        """Create event from dictionary"""
        event_type_str = data.get("event_type", "")
        try:
            event_type = NotificationEventType(event_type_str)
        except ValueError:
            event_type = NotificationEventType.INFO

        return cls(
            event_type=event_type,
            data=data.get("data"),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
            source=data.get("source", "daemon"),
            message=data.get("message"),
            metadata=data.get("metadata"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "NotificationEvent":
        """Create event from JSON string"""
        return cls.from_dict(json.loads(json_str))


# ==================== Specific Event Classes ====================


@dataclass
class ServiceEvent(NotificationEvent):
    """Base class for service lifecycle events"""

    def __init__(
        self,
        event_type: NotificationEventType,
        service_name: str,
        message: Optional[str] = None,
        error: Optional[str] = None,
        **kwargs,
    ):
        data = {"service_name": service_name}
        if error:
            data["error"] = error
        data.update(kwargs)
        super().__init__(
            event_type=event_type,
            data=data,
            source=service_name.lower(),
            message=message,
        )


@dataclass
class ServiceStartedEvent(ServiceEvent):
    """Service started successfully"""

    def __init__(self, service_name: str, **kwargs):
        super().__init__(
            event_type=NotificationEventType.SERVICE_STARTED,
            service_name=service_name,
            message=f"{service_name} started successfully",
            **kwargs,
        )


@dataclass
class ServiceStoppedEvent(ServiceEvent):
    """Service stopped"""

    def __init__(self, service_name: str, **kwargs):
        super().__init__(
            event_type=NotificationEventType.SERVICE_STOPPED,
            service_name=service_name,
            message=f"{service_name} stopped",
            **kwargs,
        )


@dataclass
class ServiceErrorEvent(ServiceEvent):
    """Service encountered an error"""

    def __init__(self, service_name: str, error: str, **kwargs):
        super().__init__(
            event_type=NotificationEventType.SERVICE_ERROR,
            service_name=service_name,
            error=error,
            message=f"{service_name} error: {error}",
            **kwargs,
        )


@dataclass
class ConnectionEvent(NotificationEvent):
    """Base class for connection events"""

    def __init__(
        self,
        event_type: NotificationEventType,
        connection_data: Optional[Dict[str, Any]] = None,
        message: Optional[str] = None,
        **kwargs,
    ):
        data = connection_data or {}
        data.update(kwargs)
        super().__init__(event_type=event_type, data=data, message=message)


@dataclass
class ConnectedEvent(ConnectionEvent):
    """Successfully connected to peer"""

    def __init__(self, connection_data: Optional[Dict[str, Any]] = None):
        super().__init__(
            event_type=NotificationEventType.CONNECTED,
            connection_data=connection_data,
            message="Successfully connected",
        )


@dataclass
class DisconnectedEvent(ConnectionEvent):
    """Disconnected from peer"""

    def __init__(self, connection_data: Optional[Dict[str, Any]] = None):
        super().__init__(
            event_type=NotificationEventType.DISCONNECTED,
            connection_data=connection_data,
            message="Disconnected",
        )


@dataclass
class ConnectionErrorEvent(ConnectionEvent):
    """Connection error"""

    def __init__(self, peer: str, error: str, **kwargs):
        super().__init__(
            event_type=NotificationEventType.CONNECTION_ERROR,
            peer=peer,
            message=f"Connection error with {peer}: {error}",
            error=error,
            **kwargs,
        )


@dataclass
class OtpGeneratedEvent(NotificationEvent):
    """OTP generated for authentication"""

    def __init__(self, otp: str, timeout: int | float = -1, **kwargs):
        data = {"otp": otp, "timeout": timeout}
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.OTP_GENERATED,
            data=data,
            message="OTP generated for authentication",
        )


@dataclass
class OtpNeededEvent(NotificationEvent):
    """OTP is required for authentication"""

    def __init__(self, needed: bool, **kwargs):
        data = {"needed": needed}
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.OTP_NEEDED,
            data=data,
            message="OTP required for authentication",
        )


@dataclass
class OtpValidatedEvent(NotificationEvent):
    """OTP validated successfully"""

    def __init__(self, **kwargs):
        super().__init__(
            event_type=NotificationEventType.OTP_VALIDATED,
            data=kwargs,
            message="OTP validated successfully",
        )


@dataclass
class OtpInvalidEvent(NotificationEvent):
    """OTP validation failed"""

    def __init__(self, reason: Optional[str] = None, **kwargs):
        message = "Invalid OTP"
        if reason:
            message += f": {reason}"
            kwargs["reason"] = reason
        super().__init__(
            event_type=NotificationEventType.OTP_INVALID, data=kwargs, message=message
        )


@dataclass
class ServerListFoundEvent(NotificationEvent):
    """Servers discovered on the network"""

    def __init__(self, servers: list, **kwargs):
        data = {"servers": servers, "count": len(servers)}
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.SERVER_LIST_FOUND,
            data=data,
            message=f"Found {len(servers)} server(s)",
        )


@dataclass
class ServerChoiceNeededEvent(NotificationEvent):
    """User needs to choose a server"""

    def __init__(self, servers: list, **kwargs):
        data = {"servers": servers, "count": len(servers)}
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.SERVER_CHOICE_NEEDED,
            data=data,
            message=f"Please choose from {len(servers)} available server(s)",
        )


@dataclass
class ServerChoiceMadeEvent(NotificationEvent):
    """User selected a server"""

    def __init__(self, server_host: str, server_port: int, **kwargs):
        data = {"server_host": server_host, "server_port": server_port}
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.SERVER_CHOICE_MADE,
            data=data,
            message=f"Selected server {server_host}:{server_port}",
        )


@dataclass
class ClientConnectedEvent(NotificationEvent):
    """Client connected to server"""

    def __init__(
        self,
        client: Optional[dict] = None,
    ):
        data = client
        if data is None:
            data = {}
        hostname = data.get("hostname", "unknown")
        super().__init__(
            event_type=NotificationEventType.CLIENT_CONNECTED,
            data=data,
            source="server",
            message=f"Client {hostname} connected",
        )


@dataclass
class ClientDisconnectedEvent(NotificationEvent):
    """Client disconnected from server"""

    def __init__(
        self,
        client: Optional[dict] = None,
    ):
        data = client
        if data is None:
            data = {}
        hostname = data.get("hostname", "unknown")
        super().__init__(
            event_type=NotificationEventType.CLIENT_DISCONNECTED,
            data=data,
            source="server",
            message=f"Client {hostname} disconnected",
        )


@dataclass
class StreamEnabledEvent(NotificationEvent):
    """Stream was enabled"""

    def __init__(self, stream_type: int, **kwargs):
        data = {"stream_type": stream_type}
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.STREAM_ENABLED,
            data=data,
            message=f"Stream {stream_type} enabled",
        )


@dataclass
class StreamDisabledEvent(NotificationEvent):
    """Stream was disabled"""

    def __init__(self, stream_type: int, **kwargs):
        data = {"stream_type": stream_type}
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.STREAM_DISABLED,
            data=data,
            message=f"Stream {stream_type} disabled",
        )


@dataclass
class ConfigSavedEvent(NotificationEvent):
    """Configuration was saved"""

    def __init__(self, config_type: str, **kwargs):
        data = {"config_type": config_type}
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.CONFIG_SAVED,
            data=data,
            message=f"{config_type} configuration saved",
        )


@dataclass
class ConfigUpdatedEvent(NotificationEvent):
    """Configuration was updated"""

    def __init__(
        self, config_type: str, changes: Optional[Dict[str, Any]] = None, **kwargs
    ):
        data = {"config_type": config_type}
        if changes:
            data["changes"] = changes
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.CONFIG_UPDATED,
            data=data,
            message=f"{config_type} configuration updated",
        )


@dataclass
class StatusUpdateEvent(NotificationEvent):
    """General status update"""

    def __init__(self, status: Dict[str, Any], message: Optional[str] = None, **kwargs):
        data = {"status": status}
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.STATUS_UPDATE,
            data=data,
            message=message or "Status updated",
        )


@dataclass
class FileTransferStartedEvent(NotificationEvent):
    """File transfer started"""

    def __init__(self, file_name: str, file_size: int, transfer_id: str, **kwargs):
        data = {
            "file_name": file_name,
            "file_size": file_size,
            "transfer_id": transfer_id,
        }
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.FILE_TRANSFER_STARTED,
            data=data,
            message=f"Transfer started: {file_name}",
        )


@dataclass
class FileTransferProgressEvent(NotificationEvent):
    """File transfer progress update"""

    def __init__(
        self,
        transfer_id: str,
        bytes_transferred: int,
        total_bytes: int,
        progress_percent: float,
        **kwargs,
    ):
        data = {
            "transfer_id": transfer_id,
            "bytes_transferred": bytes_transferred,
            "total_bytes": total_bytes,
            "progress_percent": progress_percent,
        }
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.FILE_TRANSFER_PROGRESS,
            data=data,
            message=f"Transfer progress: {progress_percent:.1f}%",
        )


@dataclass
class FileTransferCompletedEvent(NotificationEvent):
    """File transfer completed"""

    def __init__(self, transfer_id: str, file_name: str, **kwargs):
        data = {"transfer_id": transfer_id, "file_name": file_name}
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.FILE_TRANSFER_COMPLETED,
            data=data,
            message=f"Transfer completed: {file_name}",
        )


@dataclass
class FileTransferFailedEvent(NotificationEvent):
    """File transfer failed"""

    def __init__(self, transfer_id: str, file_name: str, error: str, **kwargs):
        data = {"transfer_id": transfer_id, "file_name": file_name, "error": error}
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.FILE_TRANSFER_FAILED,
            data=data,
            message=f"Transfer failed: {file_name} - {error}",
        )


@dataclass
class ScreenChangedEvent(NotificationEvent):
    """Active screen changed"""

    def __init__(self, from_screen: Optional[str], to_screen: Optional[str], **kwargs):
        data = {"from_screen": from_screen, "to_screen": to_screen}
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.SCREEN_CHANGED,
            data=data,
            message=f"Screen changed: {from_screen} -> {to_screen}",
        )


@dataclass
class ErrorEvent(NotificationEvent):
    """General error event"""

    def __init__(self, error: str, context: Optional[str] = None, **kwargs):
        data = {"error": error}
        if context:
            data["context"] = context
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.ERROR,
            data=data,
            message=f"Error: {error}",
        )


@dataclass
class WarningEvent(NotificationEvent):
    """General warning event"""

    def __init__(self, warning: str, context: Optional[str] = None, **kwargs):
        data = {"warning": warning}
        if context:
            data["context"] = context
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.WARNING,
            data=data,
            message=f"Warning: {warning}",
        )


@dataclass
class InfoEvent(NotificationEvent):
    """General info event"""

    def __init__(self, info: str, **kwargs):
        data = {"info": info}
        data.update(kwargs)
        super().__init__(event_type=NotificationEventType.INFO, data=data, message=info)


@dataclass
class CommandSuccessEvent(NotificationEvent):
    """Command executed successfully"""

    def __init__(
        self,
        command: str,
        message: Optional[str] = None,
        result_data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        data = {"command": command}
        if result_data:
            data["result"] = result_data
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.COMMAND_SUCCESS,
            data=data,
            message=message or f"Command {command} executed successfully",
        )


@dataclass
class CommandErrorEvent(NotificationEvent):
    """Command execution failed"""

    def __init__(self, command: str, error: str, **kwargs):
        data = {"command": command, "error": error}
        data.update(kwargs)
        super().__init__(
            event_type=NotificationEventType.COMMAND_ERROR,
            data=data,
            message=f"Command {command} failed: {error}",
        )


class NotificationManager:
    """
    Manager for handling notification events.

    This class is responsible for creating and sending notification events
    to registered callbacks (typically to the daemon for broadcasting).
    """

    def __init__(
        self, callback: Optional[Callable[[NotificationEvent], Awaitable[None]]] = None
    ):
        """
        Initialize notification manager.

        Args:
            callback: Async callback function to invoke when an event is generated
        """
        self._callback = callback
        self._enabled = True

    def set_callback(
        self, callback: Callable[[NotificationEvent], Awaitable[None]]
    ) -> None:
        """Set or update the callback function"""
        self._callback = callback

    def enable(self) -> None:
        """Enable notification sending"""
        self._enabled = True

    def disable(self) -> None:
        """Disable notification sending"""
        self._enabled = False

    def is_enabled(self) -> bool:
        """Check if notifications are enabled"""
        return self._enabled

    async def send(self, event: NotificationEvent) -> None:
        """
        Send a notification event object.

        Args:
            event: NotificationEvent or subclass to send
        """
        if not self._enabled or self._callback is None:
            return

        try:
            if event.event_type != NotificationEventType.PONG:
                print(f"Sending notification: {event.to_json()}")
            await self._callback(event)
        except Exception as e:
            import sys

            print(f"Error sending notification: {e}", file=sys.stderr)

    async def notify_event(self, event: NotificationEvent) -> None:
        """
        Send a notification event object (alias for send).

        Args:
            event: NotificationEvent or subclass to send
        """
        await self.send(event)

    async def notify(
        self,
        event_type: NotificationEventType,
        data: Optional[Dict[str, Any]] = None,
        message: Optional[str] = None,
        source: str = "daemon",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Send a notification event (legacy method, prefer using specific event classes).

        Args:
            event_type: Type of event
            data: Event-specific data
            message: Optional human-readable message
            source: Source service generating the event
            metadata: Additional metadata
        """
        if not self._enabled or self._callback is None:
            return

        event = NotificationEvent(
            event_type=event_type,
            data=data,
            timestamp=datetime.now().isoformat(),
            source=source,
            message=message,
            metadata=metadata,
        )

        await self.send(event)

    # Convenience methods using specific event classes

    async def notify_service_started(self, service_name: str, **kwargs) -> None:
        """Notify that a service has started"""
        await self.send(ServiceStartedEvent(service_name=service_name, **kwargs))

    async def notify_service_stopped(self, service_name: str, **kwargs) -> None:
        """Notify that a service has stopped"""
        await self.send(ServiceStoppedEvent(service_name=service_name, **kwargs))

    async def notify_service_error(
        self, service_name: str, error: str, **kwargs
    ) -> None:
        """Notify that a service encountered an error"""
        await self.send(
            ServiceErrorEvent(service_name=service_name, error=error, **kwargs)
        )

    async def notify_connected(
        self, connection_data: Optional[Dict[str, Any]] = None
    ) -> None:
        """Notify successful connection"""
        await self.send(ConnectedEvent(connection_data))

    async def notify_disconnected(
        self, peer: str, connection_data: Optional[Dict[str, Any]] = None
    ) -> None:
        """Notify disconnection"""
        await self.send(DisconnectedEvent(connection_data))

    async def notify_otp_needed(self, needed: bool, **kwargs) -> None:
        """Notify that OTP is needed"""
        await self.send(OtpNeededEvent(needed=needed, **kwargs))

    async def notify_server_list_found(self, servers: list, **kwargs) -> None:
        """Notify that servers were discovered"""
        await self.send(ServerListFoundEvent(servers=servers, **kwargs))

    async def notify_server_choice_needed(self, servers: list, **kwargs) -> None:
        """Notify that user needs to choose a server"""
        await self.send(ServerChoiceNeededEvent(servers=servers, **kwargs))

    async def notify_server_choice_made(
        self, server_host: str, server_port: int, **kwargs
    ) -> None:
        """Notify that user selected a server"""
        await self.send(
            ServerChoiceMadeEvent(
                server_host=server_host, server_port=server_port, **kwargs
            )
        )

    async def notify_client_connected(
        self,
        client: Optional[dict] = None,
    ) -> None:
        """Notify that a client connected to the server"""
        await self.send(
            ClientConnectedEvent(
                client=client,
            )
        )

    async def notify_client_disconnected(
        self,
        client: Optional[dict] = None,
    ) -> None:
        """Notify that a client disconnected from the server"""
        await self.send(
            ClientDisconnectedEvent(
                client=client,
            )
        )

    async def notify_stream_enabled(self, stream_type: int, **kwargs) -> None:
        """Notify that a stream was enabled"""
        await self.send(StreamEnabledEvent(stream_type=stream_type, **kwargs))

    async def notify_stream_disabled(self, stream_type: int, **kwargs) -> None:
        """Notify that a stream was disabled"""
        await self.send(StreamDisabledEvent(stream_type=stream_type, **kwargs))

    async def notify_config_saved(self, config_type: str, **kwargs) -> None:
        """Notify that configuration was saved"""
        await self.send(ConfigSavedEvent(config_type=config_type, **kwargs))

    async def notify_config_updated(
        self, config_type: str, changes: Optional[Dict[str, Any]] = None, **kwargs
    ) -> None:
        """Notify that configuration was updated"""
        await self.send(
            ConfigUpdatedEvent(config_type=config_type, changes=changes, **kwargs)
        )

    async def notify_status_update(
        self, status: Dict[str, Any], message: Optional[str] = None, **kwargs
    ) -> None:
        """Notify general status update"""
        await self.send(StatusUpdateEvent(status=status, message=message, **kwargs))

    async def notify_error(
        self, error: str, context: Optional[str] = None, **kwargs
    ) -> None:
        """Notify an error"""
        await self.send(ErrorEvent(error=error, context=context, **kwargs))

    async def notify_warning(
        self, warning: str, context: Optional[str] = None, **kwargs
    ) -> None:
        """Notify a warning"""
        await self.send(WarningEvent(warning=warning, context=context, **kwargs))

    async def notify_info(self, info: str, **kwargs) -> None:
        """Notify general information"""
        await self.send(InfoEvent(info=info, **kwargs))

    async def notify_command_success(
        self,
        command: str,
        message: Optional[str] = None,
        result_data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> None:
        """Notify that a command executed successfully"""
        await self.send(
            CommandSuccessEvent(
                command=command, message=message, result_data=result_data, **kwargs
            )
        )

    async def notify_command_error(self, command: str, error: str, **kwargs) -> None:
        """Notify that a command execution failed"""
        await self.send(CommandErrorEvent(command=command, error=error, **kwargs))

    async def notify_pong(self) -> None:
        """Notify pong response with latency"""
        await self.send(
            NotificationEvent(
                event_type=NotificationEventType.PONG,
                message="pong",
            )
        )
