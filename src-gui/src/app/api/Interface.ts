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
}

export enum StreamType {
    Mouse = 1,
    Keyboard = 4,
    Clipboard = 12,
}

// -- Multi-monitor layout types --
// Mirror of utils.screen._monitor on the Python side. Coordinates are
// in the OS global display coordinate space.
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
    // Half-open [start, end) interval in [0, 1] along the edge's
    // secondary axis (Y for LEFT/RIGHT, X for TOP/BOTTOM).
    segment_start: number;
    segment_end: number;
}

export interface LayoutBinding {
    slot: LayoutSlot;
    client_uid: string;
    // Pin the routed cursor to a specific monitor on the client
    // null = client picks.
    client_monitor_id: number | null;
}

// New workspace model (replaces the old edge+segment slots for the GUI).
// Each placement positions ONE client monitor inside the unified virtual
// workspace; server monitors stay at their native OS coordinates and act
// as the anchor. Cross-screen routing follows from adjacency in this
// workspace, not from a fixed LEFT/RIGHT/TOP/BOTTOM enum.
export interface MonitorPlacement {
    client_uid: string;
    client_monitor_id: number;
    // Top-left corner in the server's virtual workspace coord space (in
    // OS pixels). Width / height are mirrored from the client's
    // advertised MonitorInfo so the box keeps its aspect ratio when the
    // user moves it.
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
    // Legacy directional hint (top/right/bottom/left/center). Retained
    // as optional metadata for backwards compatibility with older
    // configs; the runtime uses ``placements`` for routing and the
    // GUI no longer renders or prompts for this value.
    screen_position?: string;
    ssl: boolean;
    streams_enabled: number[];
    is_connected: boolean;
    first_connection_date: string;
    last_connection_date: string;
    // Per-monitor info advertised by the client on the latest handshake.
    // Empty / undefined when running against a legacy client that
    // doesn't advertise its monitor layout yet.
    monitors?: MonitorInfo[];
    // Persisted workspace placements of this client's monitors in the
    // server's unified workspace. Sourced from the daemon config so the
    // GUI can seed the layout editor on startup.
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
    // Local monitor list as enumerated by the daemon on the server
    // machine. Empty when the OS backend can't enumerate displays;
    // the GUI falls back to a single virtual monitor in that case.
    monitors?: MonitorInfo[];
}

export interface ClientConnectionInfo {
    uid: string;
    host: string;
    hostname: string;
    port: number;
    ssl: boolean;
    auto_reconnect: boolean;
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
    // Legacy field — kept optional for backward compatibility. New
    // approvals don't carry a position; the GUI auto-opens the Layout
    // Editor instead.
    screen_position?: string;
    reason: string;
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