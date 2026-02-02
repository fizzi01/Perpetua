
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
    SslHandshakeStarted,
    SslHandshakeCompleted,
    SslHandshakeFailed,
    CertificateShared,
    CertificateReceived,

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

    // SSL/Certificate management
    EnableSsl,
    DisableSsl,
    ShareCertificate,
    ReceiveCertificate,
    SetOtp,

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

// -- Status Interfaces --
export interface ClientObj {
  uid: string;
  host_name: string;
  ip_address: string;
  screen_position: string;
  ssl: boolean;
  streams_enabled: number[];
  is_connected: boolean;
  first_connection_date: string;
  last_connection_date: string;
}

export interface ClientEditObj {
  hostname?: string;
  ip_address?: string;
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
}

export interface ClientConnectionInfo {
  uid: string;
  host: string;
  hostname: string;
  port: number;
  ssl: boolean;
  auto_reconnect: boolean;
}

export interface ClientStatus
{
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