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
use std::{env::VarError, io, path::PathBuf, time::Duration};
use thiserror::Error;

#[cfg(unix)]
use std::{env, path::Path};
#[cfg(unix)]
use tokio::net::UnixStream;

#[cfg(windows)]
use tokio::net::TcpStream;

#[cfg(windows)]
const DEFAULT_DAEMON_PORT: u16 = 55652;

pub const DEFAULT_APP_DIR: &str = "Perpetua";

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

fn default_path() -> Result<DefaultPath, SocketPathError> {
    #[cfg(all(unix, target_os = "macos"))]
    {
        let home = env::var("HOME").map_err(SocketPathError::HomeDirNotFound)?;

        Ok(DefaultPath::Unix(
            Path::new(&home)
                .join("Library")
                .join("Caches")
                .join(DEFAULT_APP_DIR)
                .join(DEFAULT_DAEMON_SOCKET),
        ))
    }

    #[cfg(windows)]
    {
        Ok(DefaultPath::Tcp(
            "127.0.0.1".to_string(),
            DEFAULT_DAEMON_PORT,
        ))
    }
}

async fn wait_connection(t: Duration, max_t: Duration) -> Result<(AsyncReader, AsyncWriter), ConnectionError> {
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

pub async fn connect(t: Duration, max_t: Duration) -> Result<(AsyncReader, AsyncWriter), ConnectionError> {
    let Ok((reader, writer)) = wait_connection(t, max_t).await else {
        return Err(ConnectionError::Timeout);
    };
    Ok((reader, writer))
}
