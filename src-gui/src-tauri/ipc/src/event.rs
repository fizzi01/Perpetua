use std::default;

use serde_json::{Value};
use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Debug, PartialEq)]
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

    // Command result events
    CommandSuccess,
    CommandError,

    #[serde(other)]
    Other
}

#[derive(Serialize, Deserialize, Debug)]
pub struct NotificationEvent {
    pub event_type: EventType,
    pub data: Option<Value>,
    pub timestamp: String,
    pub source: String,
    pub message: Option<String>,
    pub metadata: Option<Value>,
}

pub struct EventParser;

pub trait Parser<T> {
    fn parse_json(input: &str) -> Option<T>;
}

impl Parser<NotificationEvent> for EventParser {
    fn parse_json(input: &str) -> Option<NotificationEvent> {
        match serde_json::from_str::<NotificationEvent>(input) {
            Ok(event) => Some(event),
            Err(_) => None,
        }
    }
}