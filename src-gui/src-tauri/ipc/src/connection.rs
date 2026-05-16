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

use crate::{AsyncReader, AsyncWriter};
use std::{env, env::VarError, fs, io, path::PathBuf, time::Duration};
use thiserror::Error;

#[cfg(unix)]
use std::path::Path;
#[cfg(unix)]
use tokio::net::UnixStream;

#[cfg(windows)]
use tokio::net::TcpStream;

#[cfg(windows)]
const DEFAULT_DAEMON_PORT: u16 = 55652;

const RUNTIME_DIR: &str = "runtime";
const ENDPOINT_FILE: &str = "daemon.endpoint";
const ENV_ENDPOINT: &str = "PERPETUA_DAEMON_ENDPOINT";

#[cfg(not(target_os = "linux"))]
pub const DEFAULT_APP_DIR: &str = "Perpetua";

#[cfg(target_os = "linux")]
pub const DEFAULT_APP_DIR: &str = ".perpetua";

#[cfg(unix)]
pub const DEFAULT_DAEMON_SOCKET: &str = "perpetua_daemon.sock";

#[derive(Debug, Error)]
pub enum ConnectionError {
    #[error(transparent)]
    SocketPath(#[from] SocketPathError),
    #[error(transparent)]
    Io(#[from] io::Error),
    #[error("connection timed out")]
    Timeout,
}

#[derive(Debug, Error)]
pub enum SocketPathError {
    #[error("could not determine $HOME: `{0}`")]
    HomeDirNotFound(VarError),
}

#[allow(unused)]
enum DefaultPath {
    Unix(PathBuf),
    Tcp(String, u16),
}

/// Return the directory the daemon writes its runtime files into. Same logic
/// as the Python side's ``ApplicationConfig.get_main_path()`` plus a
/// ``runtime/`` subdirectory.
fn runtime_dir() -> Result<PathBuf, SocketPathError> {
    let home = env::var("HOME").map_err(SocketPathError::HomeDirNotFound)?;
    let main = if cfg!(target_os = "macos") {
        PathBuf::from(&home)
            .join("Library")
            .join("Caches")
            .join(DEFAULT_APP_DIR)
    } else if cfg!(target_os = "linux") {
        PathBuf::from(&home).join(DEFAULT_APP_DIR)
    } else {
        // Windows: $LOCALAPPDATA\Perpetua. Fall back to %USERPROFILE% so we
        // still produce something usable if LOCALAPPDATA isn't set.
        let base = env::var("LOCALAPPDATA")
            .or_else(|_| env::var("USERPROFILE"))
            .unwrap_or_else(|_| home.clone());
        PathBuf::from(base).join("AppData").join("Local").join(DEFAULT_APP_DIR)
    };
    Ok(main.join(RUNTIME_DIR))
}

#[derive(serde::Deserialize)]
struct EndpointPayload {
    endpoint: String,
}

/// Parse a ``tcp://host:port`` or ``unix:///path`` string into a DefaultPath.
///
/// The IPC pipeline is generic over a single transport per platform (TCP on
/// Windows, Unix socket elsewhere), so endpoints not matching the host
/// platform's transport are rejected - callers fall back to the platform
/// default instead of producing an unreachable variant.
fn parse_endpoint(s: &str) -> Option<DefaultPath> {
    let s = s.trim();
    if let Some(rest) = s.strip_prefix("tcp://") {
        if cfg!(unix) {
            return None;
        }
        let (host, port) = rest.rsplit_once(':')?;
        let port: u16 = port.parse().ok()?;
        return Some(DefaultPath::Tcp(host.to_string(), port));
    }
    if let Some(rest) = s.strip_prefix("unix://") {
        if cfg!(windows) {
            return None;
        }
        return Some(DefaultPath::Unix(PathBuf::from(rest)));
    }
    // Accept bare ``host:port`` and bare filesystem path as a courtesy.
    if cfg!(windows) {
        if let Some((host, port)) = s.rsplit_once(':') {
            if let Ok(port) = port.parse::<u16>() {
                return Some(DefaultPath::Tcp(host.to_string(), port));
            }
        }
    }
    if cfg!(unix) && s.starts_with('/') {
        return Some(DefaultPath::Unix(PathBuf::from(s)));
    }
    None
}

/// Try to discover the daemon's IPC endpoint via the runtime file the daemon
/// writes after a successful bind. Returns None if the file is absent or
/// malformed - callers should then fall back to the legacy default.
fn discover_endpoint_file() -> Option<DefaultPath> {
    let dir = runtime_dir().ok()?;
    let path = dir.join(ENDPOINT_FILE);
    let raw = fs::read_to_string(&path).ok()?;
    let payload: EndpointPayload = serde_json::from_str(&raw).ok()?;
    parse_endpoint(&payload.endpoint)
}

fn default_path() -> Result<DefaultPath, SocketPathError> {
    // 1. Explicit env-var override (dev / containers / multi-instance).
    if let Ok(val) = env::var(ENV_ENDPOINT) {
        if !val.trim().is_empty() {
            if let Some(parsed) = parse_endpoint(&val) {
                return Ok(parsed);
            }
        }
    }

    // 2. Runtime endpoint file written by the daemon after bind. Keeps the
    //    GUI in sync with the actual port (Windows fallback) or socket path
    //    (custom config).
    if let Some(found) = discover_endpoint_file() {
        return Ok(found);
    }

    // 3. Platform default - same as before.
    #[cfg(unix)]
    {
        let home = env::var("HOME").map_err(SocketPathError::HomeDirNotFound)?;

        #[cfg(target_os = "macos")]
        return Ok(DefaultPath::Unix(
            Path::new(&home)
                .join("Library")
                .join("Caches")
                .join(DEFAULT_APP_DIR)
                .join(DEFAULT_DAEMON_SOCKET),
        ));

        #[cfg(not(target_os = "macos"))]
        return Ok(DefaultPath::Unix(
            Path::new(&home)
                .join(DEFAULT_APP_DIR)
                .join(DEFAULT_DAEMON_SOCKET),
        ));
    }

    #[cfg(windows)]
    {
        Ok(DefaultPath::Tcp(
            "127.0.0.1".to_string(),
            DEFAULT_DAEMON_PORT,
        ))
    }
}

async fn wait_connection(
    t: Duration,
    max_t: Duration,
) -> Result<(AsyncReader, AsyncWriter), ConnectionError> {
    let path = default_path()?;
    let mut timeout = t;

    loop {
        match &path {
            DefaultPath::Unix(_socket_path) => {
                #[cfg(unix)]
                {
                    if let Ok(stream) = UnixStream::connect(_socket_path).await {
                        let (r, w) = stream.into_split();
                        let reader = AsyncReader::new(r);
                        let writer = AsyncWriter::new(w);
                        return Ok((reader, writer));
                    }
                }
            }
            DefaultPath::Tcp(_addr, _port) => {
                #[cfg(windows)]
                {
                    if let Ok(stream) = TcpStream::connect((_addr.as_str(), *_port)).await {
                        let (r, w) = stream.into_split();
                        let reader = AsyncReader::new(r);
                        let writer = AsyncWriter::new(w);
                        return Ok((reader, writer));
                    }
                }
            }
        }
        if timeout == max_t {
            return Err(ConnectionError::Timeout);
        }
        tokio::time::sleep(timeout).await;
        timeout = std::cmp::min(timeout * 2, max_t);
    }
}

pub async fn connect(
    t: Duration,
    max_t: Duration,
) -> Result<(AsyncReader, AsyncWriter), ConnectionError> {
    let Ok((reader, writer)) = wait_connection(t, max_t).await else {
        return Err(ConnectionError::Timeout);
    };
    Ok((reader, writer))
}
