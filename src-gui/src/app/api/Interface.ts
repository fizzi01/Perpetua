
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