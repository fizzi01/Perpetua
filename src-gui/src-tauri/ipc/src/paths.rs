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

//! Shared filesystem-path resolution for daemon discovery.
//!
//! Mirrors the Python side ``ApplicationConfig`` (``src/config/__init__.py``)
//! so the GUI looks for the daemon's runtime files and logs in the same
//! locations the daemon writes them. On Linux that means following the XDG
//! Base Directory Specification, with a fallback to the legacy
//! ``~/.perpetua`` layout for pre-migration installs.

use std::path::PathBuf;

#[cfg(any(target_os = "linux", target_os = "windows"))]
use std::env;

#[cfg(not(target_os = "linux"))]
pub const APP_DIR: &str = "Perpetua";

/// XDG-compliant app dir name on Linux (lower-case, under
/// ``$XDG_CONFIG_HOME``/``$XDG_STATE_HOME``/``$XDG_RUNTIME_DIR``).
#[cfg(target_os = "linux")]
pub const APP_DIR: &str = "perpetua";

/// Legacy pre-XDG layout: ``~/.perpetua``. The Python daemon migrates files
/// out of this on first start; we keep it as a discovery fallback.
#[cfg(target_os = "linux")]
pub const LEGACY_LINUX_APP_DIR: &str = ".perpetua";

#[cfg(unix)]
pub const DAEMON_SOCKET: &str = "perpetua_daemon.sock";

pub const RUNTIME_SUBDIR: &str = "runtime";
pub const ENDPOINT_FILE: &str = "daemon.endpoint";
pub const DAEMON_LOG: &str = "daemon.log";

fn home_dir() -> Option<PathBuf> {
    dirs::home_dir()
}

/// Candidate directories where the daemon stores persistent state
/// (log file, endpoint file). Ordered by preference.
///
/// On Linux the XDG state dir is primary, with the legacy ``~/.perpetua``
/// as a second-chance fallback. On macOS / Windows there is a single
/// canonical location.
pub fn state_dirs() -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = Vec::new();
    let Some(home) = home_dir() else {
        return dirs;
    };

    #[cfg(target_os = "macos")]
    {
        dirs.push(home.join("Library").join("Caches").join(APP_DIR));
    }

    #[cfg(target_os = "windows")]
    {
        let base = env::var("LOCALAPPDATA")
            .ok()
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join("AppData").join("Local"));
        dirs.push(base.join(APP_DIR));
    }

    #[cfg(target_os = "linux")]
    {
        let state_home = env::var("XDG_STATE_HOME")
            .ok()
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join(".local").join("state"));
        dirs.push(state_home.join(APP_DIR));
        dirs.push(home.join(LEGACY_LINUX_APP_DIR));
    }

    dirs
}

/// Candidate directories where the daemon writes its endpoint discovery file
/// (``<state>/runtime/daemon.endpoint``). Ordered by preference.
pub fn runtime_dirs() -> Vec<PathBuf> {
    state_dirs()
        .into_iter()
        .map(|d| d.join(RUNTIME_SUBDIR))
        .collect()
}

/// Candidate default Unix socket paths the daemon may have bound to when no
/// endpoint discovery file is available. Ordered by preference.
#[cfg(unix)]
pub fn default_socket_paths() -> Vec<PathBuf> {
    let mut paths: Vec<PathBuf> = Vec::new();

    #[cfg(target_os = "linux")]
    {
        // Prefer ``$XDG_RUNTIME_DIR/perpetua/<sock>`` (tmpfs, per-session).
        if let Ok(rt) = env::var("XDG_RUNTIME_DIR") {
            if !rt.is_empty() {
                paths.push(PathBuf::from(rt).join(APP_DIR).join(DAEMON_SOCKET));
            }
        }
    }

    // Fall back to one socket file per state directory.
    for d in state_dirs() {
        paths.push(d.join(DAEMON_SOCKET));
    }
    paths
}

/// Candidate paths to the daemon log file, in preference order.
pub fn log_file_paths() -> Vec<PathBuf> {
    state_dirs()
        .into_iter()
        .map(|d| d.join(DAEMON_LOG))
        .collect()
}
