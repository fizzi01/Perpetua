use serde_json::{Value};
use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Debug,Clone, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum EventType {
    // Service lifecycle events
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

    #[serde(other)]
    Other
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct NotificationEvent {
    pub event_type: EventType,
    pub data: Option<Value>,
    pub timestamp: String,
    pub source: String,
    pub message: Option<String>,
    pub metadata: Option<Value>,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum CommandType {
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

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct CommandEvent {
    pub command: CommandType,
    pub params: Option<Value>,
}

pub trait Event: Serialize + for<'de> Deserialize<'de> {}

impl Event for NotificationEvent {}
impl Event for CommandEvent {}

pub struct EventParser;

pub trait Parser<'a, T>
where
    T: Event,
    {
    /// Parse a JSON string into an event of type T.
    fn parse_json(input: &'a str) -> Result<T, serde_json::Error>;

    /// Serialize an event of type T into a JSON string.
    fn serialize(event: &T) -> Result<String, serde_json::Error>
    where
        T: Serialize,
    {
        serde_json::to_string(event)
    }
}

impl<'a,T: Event> Parser<'a, T> for EventParser {
    fn parse_json(input: &'a str) -> Result<T, serde_json::Error>
     {
        match serde_json::from_str::<T>(input) {
            Ok(event) => Ok(event),
            Err(e) => Err(e),
        }
    }

    fn serialize(event: &T) -> Result<String, serde_json::Error>
        where
            T: Serialize, {
        serde_json::to_string(event)
    }
}