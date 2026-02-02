/*
 Perpetua - open-source and cross-platform KVM software.
 Copyright (c) 2026 Federico Izzi

 This program is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.

 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with this program. If not, see <https://www.gnu.org/licenses/>.
 */

use std::fmt::Display;

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub trait Event: Serialize + for<'de> Deserialize<'de> {}
pub trait Type: for<'de> Deserialize<'de> {}

pub trait TypeToString
where
    Self: Type,
{
    fn to_string(&self) -> Result<&'static str, serde_variant::UnsupportedType>;
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
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
    Other,
}

impl Type for EventType {}

impl TypeToString for EventType {
    fn to_string(&self) -> Result<&'static str, serde_variant::UnsupportedType> {
        serde_variant::to_variant_name(&self)
    }
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

impl Type for CommandType {}

impl TypeToString for CommandType {
    fn to_string(&self) -> Result<&'static str, serde_variant::UnsupportedType> {
        serde_variant::to_variant_name(&self)
    }
}

impl Display for CommandType
where
    Self: TypeToString,
{
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match TypeToString::to_string(self) {
            Ok(name) => write!(f, "{}", name),
            Err(_) => write!(f, "unknown_command"),
        }
    }
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct CommandEvent {
    pub command: CommandType,
    pub params: Option<Value>,
}

impl CommandEvent {
    pub fn build(command: CommandType, params: &str) -> Self {
        let params_value: Option<Value> = if params.is_empty() {
            None
        } else {
            serde_json::from_str(params).ok()
        };

        CommandEvent {
            command,
            params: params_value,
        }
    }

    pub fn empty(command: CommandType) -> Self {
        CommandEvent {
            command,
            params: None,
        }
    }

    pub fn new(command: CommandType, params: Option<Value>) -> Self {
        CommandEvent { command, params }
    }
}

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

impl<'a, T: Event> Parser<'a, T> for EventParser {
    fn parse_json(input: &'a str) -> Result<T, serde_json::Error> {
        match serde_json::from_str::<T>(input) {
            Ok(event) => Ok(event),
            Err(e) => Err(e),
        }
    }

    fn serialize(event: &T) -> Result<String, serde_json::Error>
    where
        T: Serialize,
    {
        serde_json::to_string(event)
    }
}
