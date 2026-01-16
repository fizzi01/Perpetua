use crate::{AsyncReader, AsyncWriter};
use std::{
    env::{self, VarError},
    io,
    path::{Path, PathBuf},
    time::Duration,
};
use thiserror::Error;

#[cfg(unix)]
use tokio::net::UnixStream;

#[cfg(windows)]
use tokio::net::TcpStream;

#[cfg(windows)]
const DEFAULT_DAEMON_PORT: u16 = 55655;

#[cfg(unix)]
pub const DEFAULT_APP_DIR: &str = "PyContinuity";
#[cfg(unix)]
pub const DEFAULT_DAEMON_SOCKET: &str = "pycontinuity_daemon.sock";

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

async fn wait_connection(t: Duration) -> Result<(AsyncReader, AsyncWriter), ConnectionError> {
    let path = default_path()?;
    let mut timeout = t;
    let max_attempts = 4;
    let mut attempts = 0;

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
        if attempts >= max_attempts {
            return Err(ConnectionError::Timeout);
        }
        attempts += 1;
        tokio::time::sleep(timeout).await;
        timeout = std::cmp::min(timeout * 2, Duration::from_secs(1));
    }
}

pub async fn connect(t: Duration) -> Result<(AsyncReader, AsyncWriter), ConnectionError> {
    let Ok((reader, writer)) = wait_connection(t).await else {
        return Err(ConnectionError::Timeout);
    };
    Ok((reader, writer))
}
