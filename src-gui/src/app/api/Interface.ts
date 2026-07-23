/*
 * Perpetua - open-source and cross-platform KVM software.
 * Copyright (c) 2026 Federico Izzi.
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 *
 */

export interface CommandPayload {
    data?: {
        command?: string;
        result?: any;
        error?: string;
    };
    message?: string;
}

export interface GeneralEvent {
    data?: object;
    message?: string;
}

export enum EventType {
    ServiceInitialized,
    ServiceStarting,
    ServiceStarted,
    ServiceStopping,
    ServiceStopped,
    ServiceError,

    // Connection events
    Connecting,
    Connected,
    Disconnected,
    ConnectionError,
    ConnectionLost,
    Reconnecting,
    Reconnected,

    // Server discovery events (client)
    DiscoveryStarted,
    ServerListFound,
    ServerDiscovered,
    DiscoveryCompleted,
    DiscoveryTimeout,

    // Authentication events
    OtpNeeded,
    OtpValidated,
    OtpInvalid,
    OtpGenerated,
    PairingRequested,
    SslHandshakeStarted,
    SslHandshakeCompleted,
    SslHandshakeFailed,
    CertificateShared,
    CertificateReceived,
    CertificateStale,

    // Server choice events (client)
    ServerChoiceNeeded,
    ServerChoiceMade,

    // Client management events (server)
    ClientConnected,
    ClientDisconnected,
    ClientAuthenticated,
    ClientAdded,
    ClientRemoved,
    ClientUpdated,
    ClientApprovalRequested,
    ClientApprovalResolved,
    ClientRejected,

    // Stream events
    StreamEnabled,
    StreamDisabled,
    StreamsUpdated,

    // Configuration events
    ConfigLoaded,
    ConfigSaved,
    ConfigUpdated,
    ConfigError,

    // State change events
    StateChanged,
    ModeChanged,

    // Screen events
    ScreenChanged,
    ScreenTransitionStarted,
    ScreenTransitionCompleted,
    MonitorTopologyChanged,

    // Transfer events
    FileTransferStarted,
    FileTransferProgress,
    FileTransferCompleted,
    FileTransferFailed,
    ClipboardSynced,

    // Network events
    NetworkLatencyHigh,
    NetworkQualityDegraded,
    NetworkQualityRestored,

    // Permission events (macOS Accessibility / Input Monitoring gate)
    PermissionsRequired,
    PermissionsGranted,

    // General events
    StatusUpdate,
    Info,
    Warning,
    Error,
    Test,
    Pong,

    // Command result events
    CommandSuccess,
    CommandError,

    // Internal event
    ShowLog,

    Other,
}

export enum CommandType {
    // Service control
    ServiceChoice,
    StartServer,
    StopServer,
    StartClient,
    StopClient,

    // Status queries
    Status,
    ServerStatus,
    ClientStatus,

    // Configuration management
    GetServerConfig,
    SetServerConfig,
    GetClientConfig,
    SetClientConfig,
    SaveConfig,
    ReloadConfig,

    // Stream management
    EnableStream,
    DisableStream,
    GetStreams,

    // Client management (server only)
    AddClient,
    RemoveClient,
    EditClient,
    ListClients,
    ApproveClient,
    DenyClient,
    ListPendingApprovals,
    SetClientLayout,

    // SSL/Certificate management
    EnableSsl,
    DisableSsl,
    ShareCertificate,
    ReceiveCertificate,
    SetOtp,
    RequestPairing,

    // Server selection (client)
    CheckServerChoiceNeeded,
    GetFoundServers,
    ChooseServer,
    CheckOtpNeeded,

    // Service discovery
    DiscoverServices,

    // Daemon control
    Shutdown,
    Ping,

    // Autostart-at-login
    GetAutostart,
    SetAutostart,

    // OS-level permissions (macOS Accessibility / Input Monitoring)
    GetPermissions,
    RequestPermissions,
}

// -- Permission gate types (macOS) --
export type PermissionStatus = "granted" | "denied" | "unknown" | "not_required";

export interface PermissionInfo {
    type: string;
    status: PermissionStatus;
    message?: string | null;
    can_request: boolean;
}

export interface PermissionsRequiredData {
    permissions: PermissionInfo[];
    pending_service?: string | null;
    // True when the permission was revoked while the app was already running
    // (as opposed to missing at startup). Drives a clearer gate message.
    revoked?: boolean;
    reason?: string;
}

export interface PermissionsResult {
    permissions: PermissionInfo[];
    missing: PermissionInfo[];
    pending_service?: string | null;
}

export enum StreamType {
    Mouse = 1,
    Keyboard = 4,
    Clipboard = 12,
}

// -- Multi-monitor layout types --
// Mirror of utils.screen._monitor on the Python side; coords are in the OS global display space.
export interface MonitorInfo {
    monitor_id: number;
    min_x: number;
    min_y: number;
    max_x: number;
    max_y: number;
    is_primary?: boolean;
    name?: string;
    scaling_factor?: number;
}

export type Edge = "left" | "right" | "top" | "bottom";

export interface LayoutSlot {
    monitor_id: number;
    edge: Edge;
    // Half-open [start, end) along the edge's secondary axis (Y for LEFT/RIGHT, X for TOP/BOTTOM).
    segment_start: number;
    segment_end: number;
}

export interface LayoutBinding {
    slot: LayoutSlot;
    client_uid: string;
    // null = client picks.
    client_monitor_id: number | null;
}

// Workspace model: places one client monitor in the server's virtual workspace; server monitors anchor it.
export interface MonitorPlacement {
    client_uid: string;
    client_monitor_id: number;
    workspace_x: number;
    workspace_y: number;
    width: number;
    height: number;
}

export interface WorkspaceLayout {
    placements: MonitorPlacement[];
}

// -- Status Interfaces --
export interface ClientObj {
    uid: string;
    host_name: string;
    ip_addresses: string[];
    // Legacy directional hint; kept for backwards compatibility, runtime uses `placements`.
    screen_position?: string;
    ssl: boolean;
    streams_enabled: number[];
    is_connected: boolean;
    first_connection_date: string;
    last_connection_date: string;
    monitors?: MonitorInfo[];
    placements?: MonitorPlacement[];
}

export interface ClientEditObj {
    hostname?: string;
    ip_addresses?: string[];
    screen_position?: string;
}

export interface ServerStatus {
    start_time?: string;
    running: boolean;
    uid: string;
    host: string;
    port: number;
    heartbeat_interval: number;
    streams_enabled: Object;
    ssl_enabled: boolean;
    authorized_clients: ClientObj[];
    monitors?: MonitorInfo[];
    // Snapshot of unknown clients currently awaiting admin approval.
    // Surfaced on STATUS so a late-launching GUI doesn't miss pending requests.
    pending_approvals?: ClientApprovalRequest[];
}

export interface CertificateMetadata {
    present: boolean;
    subject_common_name?: string | null;
    issuer_common_name?: string | null;
    valid_from?: string;
    valid_until?: string;
    expired?: boolean;
    sha256_fingerprint?: string;
    public_key_algorithm?: string;
    public_key_size?: number | null;
    error?: string;
}

export interface ClientSecurityInfo {
    ssl_enabled: boolean;
    mutual_tls_available: boolean;
    server_ca: CertificateMetadata;
    client_certificate: CertificateMetadata;
    private_key_present: boolean;
}

export interface ClientConnectionInfo {
    uid: string;
    host: string;
    hostname: string;
    port: number;
    ssl: boolean;
    auto_reconnect: boolean;
    security_info?: ClientSecurityInfo;
}

export interface ClientStatus {
    running: boolean;
    connected: boolean;
    start_time?: string;
    otp_needed: boolean;
    service_choice_needed: boolean;
    available_servers?: ServerFound[];
    uid: string;
    client_hostname: string;
    streams_enabled: number[];
    ssl_enabled: boolean;
    server_info: ClientConnectionInfo;
    security_info?: ClientSecurityInfo;
}

export interface ServiceStatus {
    server_info?: ServerStatus;
    client_info?: ClientStatus;
}

// -- SSL/OTP Interfaces --
export interface OtpInfo {
    otp: string;
    timeout: number; // in seconds
    instructions: string;
}

export interface PairingRequestInfo {
    otp: string;
    timeout: number;
    peer_ip: string;
    hostname: string;
    was_active: boolean;
}

export interface ClientApprovalRequest {
    peer_ip: string;
    hostname: string;
    uid: string;
    request_id: string;
    timeout: number;
}

export interface ClientApprovalResolved {
    peer_ip: string;
    approved: boolean;
    request_id: string;
    // Legacy — kept for backward compatibility; new approvals open the Layout Editor instead.
    screen_position?: string;
    reason: string;
}


export interface ClientRejected {
    peer_ip: string;
    reason: string;
    hostname: string;
    uid: string;
}


export interface ServerFound {
    uid: string;
    address: string;
    hostname: string;
    port: number;
}

export interface ServerChoice {
    servers: ServerFound[];
}

export interface ServiceError {
    error: string;
    service_name: string;
}
