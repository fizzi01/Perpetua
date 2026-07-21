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

use crate::paths;
use crate::{AsyncReader, AsyncWriter};
use std::{
    env,
    env::VarError,
    fs, io,
    path::PathBuf,
    time::{Duration, Instant},
};
use thiserror::Error;

#[cfg(unix)]
use tokio::net::UnixStream;

#[cfg(windows)]
use tokio::net::TcpStream;

#[cfg(windows)]
const DEFAULT_DAEMON_PORT: u16 = 55652;

const ENV_ENDPOINT: &str = "PERPETUA_DAEMON_ENDPOINT";

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
/// writes after a successful bind. Returns None if no candidate location
/// holds a readable, well-formed file - callers should then fall back to the
/// legacy default.
fn discover_endpoint_file() -> Option<DefaultPath> {
    for dir in paths::runtime_dirs() {
        let path = dir.join(paths::ENDPOINT_FILE);
        let Ok(raw) = fs::read_to_string(&path) else {
            continue;
        };
        let Ok(payload) = serde_json::from_str::<EndpointPayload>(&raw) else {
            continue;
        };
        if let Some(parsed) = parse_endpoint(&payload.endpoint) {
            return Some(parsed);
        }
    }
    None
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

    // 3. Platform default - matches the daemon's ``DEFAULT_SOCKET_PATH``.
    #[cfg(unix)]
    {
        let candidates = paths::default_socket_paths();
        if candidates.is_empty() {
            // The only way ``state_dirs()`` returns empty on unix is when the
            // home dir can't be resolved.
            return Err(SocketPathError::HomeDirNotFound(VarError::NotPresent));
        }
        // Prefer the first existing path; otherwise fall back to the first
        // candidate so the connect loop can create the socket location.
        let chosen = candidates
            .iter()
            .find(|p| p.exists())
            .cloned()
            .unwrap_or_else(|| candidates[0].clone());
        return Ok(DefaultPath::Unix(chosen));
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
    initial: Duration,
    max_interval: Duration,
    deadline: Duration,
) -> Result<(AsyncReader, AsyncWriter), ConnectionError> {
    // Resolve once up-front so a fatal, stable error (e.g. no ``$HOME``)
    // surfaces immediately instead of being retried for the whole deadline.
    default_path()?;

    let start = Instant::now();
    let mut interval = initial;

    loop {
        // Re-resolve every attempt: the daemon writes its endpoint file only
        // after binding, so on a cold boot the first attempts happen before
        // the file exists. Re-reading it here lets us pick up the real port
        // (including a Windows fallback port) as soon as it appears instead
        // of being stuck on the platform default.
        if let Ok(path) = default_path() {
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
        }

        let elapsed = start.elapsed();
        if elapsed >= deadline {
            return Err(ConnectionError::Timeout);
        }
        // Never sleep past the deadline, and cap the interval so that after the
        // initial exponential ramp-up the GUI keeps polling roughly every
        // ``max_interval`` and latches onto the daemon promptly once it binds.
        let remaining = deadline - elapsed;
        tokio::time::sleep(std::cmp::min(interval, remaining)).await;
        interval = std::cmp::min(interval * 2, max_interval);
    }
}

pub async fn connect(
    initial: Duration,
    max_interval: Duration,
    deadline: Duration,
) -> Result<(AsyncReader, AsyncWriter), ConnectionError> {
    let Ok((reader, writer)) = wait_connection(initial, max_interval, deadline).await else {
        return Err(ConnectionError::Timeout);
    };
    Ok((reader, writer))
}
