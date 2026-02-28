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

use std::env;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

/// Arguments forwarded to the daemon executable.
#[derive(Clone, Debug)]
pub struct DaemonConfig {
    pub server: bool,
    pub client: bool,
    pub socket: Option<String>,
    pub config_dir: Option<String>,
    pub log_terminal: bool,
    pub debug: bool,
}

impl DaemonConfig {
    /// Build the argument list forwarded to the daemon.
    fn to_args(&self) -> Vec<String> {
        let mut args = Vec::new();

        if self.server {
            args.push("--server".into());
        }
        if self.client {
            args.push("--client".into());
        }
        if let Some(ref socket) = self.socket {
            args.push("--socket".into());
            args.push(socket.clone());
        }
        if let Some(ref dir) = self.config_dir {
            args.push("--config-dir".into());
            args.push(dir.clone());
        }
        if self.log_terminal {
            args.push("--log-terminal".into());
        }
        if self.debug {
            args.push("--debug".into());
        }

        args
    }
}

/// Resolve the path to the `_perpetua` daemon executable, located next to this binary.
fn daemon_executable_path() -> PathBuf {
    let mut path = env::current_exe()
        .expect("Failed to determine current executable path")
        .parent()
        .expect("Executable has no parent directory")
        .to_path_buf();

    if cfg!(target_os = "windows") {
        path.push("_perpetua.exe");
    } else {
        path.push("_perpetua");
    }
    path
}

/// Forward a piped stream line-by-line to stdout in a background thread.
fn forward_stream<R: std::io::Read + Send + 'static>(
    stream: R,
    prefix: &'static str,
) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        let reader = BufReader::new(stream);
        for line in reader.lines() {
            match line {
                Ok(text) => {
                    if prefix.is_empty() {
                        println!("{}", text);
                    } else {
                        println!("{}{}", prefix, text);
                    }
                }
                Err(_) => break,
            }
        }
    })
}

/// Handle to the daemon child process, stored as Tauri managed state.
///
/// Owns the full lifecycle of the daemon: spawning, waiting, and termination.
/// In debug builds use [`DaemonProcess::empty`].
#[derive(Clone)]
pub struct DaemonProcess {
    inner: Arc<Mutex<Option<Child>>>,
}

impl DaemonProcess {
    /// Spawn the daemon executable with the given configuration.
    ///
    /// If `config.log_terminal` is set, stdout/stderr are forwarded to the
    /// current terminal via background threads.
    pub fn spawn(config: &DaemonConfig) -> Self {
        let exe = daemon_executable_path();
        if !exe.exists() {
            eprintln!("[perpetua] daemon executable not found: {}", exe.display());
            std::process::exit(1);
        }

        let mut cmd = Command::new(&exe);
        cmd.args(config.to_args());

        if config.log_terminal {
            cmd.stdout(Stdio::piped());
            cmd.stderr(Stdio::piped());
        } else {
            cmd.stdout(Stdio::null());
            cmd.stderr(Stdio::null());
        }

        // stdin is always inherited so the daemon can read from terminal if needed
        cmd.stdin(Stdio::inherit());

        let mut child = cmd.spawn().unwrap_or_else(|e| {
            eprintln!(
                "[perpetua] failed to spawn daemon ({}): {}",
                exe.display(),
                e
            );
            std::process::exit(1);
        });

        let pid = child.id();
        eprintln!("[perpetua] daemon spawned (pid {})", pid);

        // Forward piped streams in background threads
        let _stdout_handle = child.stdout.take().map(|s| forward_stream(s, ""));
        let _stderr_handle = child.stderr.take().map(|s| forward_stream(s, ""));

        Self {
            inner: Arc::new(Mutex::new(Some(child))),
        }
    }

    /// Create a no-op handle (debug mode / no daemon).
    pub fn empty() -> Self {
        Self {
            inner: Arc::new(Mutex::new(None)),
        }
    }

    /// Block until the daemon process exits.
    pub fn wait(&self) {
        let mut guard = match self.inner.lock() {
            Ok(g) => g,
            Err(poisoned) => poisoned.into_inner(),
        };
        if let Some(ref mut child) = *guard {
            let _ = child.wait();
        }
    }

    /// Terminate the daemon process gracefully (SIGTERM on Unix, kill on Windows).
    /// This is idempotent — calling it more than once is safe.
    pub fn terminate(&self) {
        let mut guard = match self.inner.lock() {
            Ok(g) => g,
            Err(poisoned) => poisoned.into_inner(),
        };

        if let Some(ref mut child) = *guard {
            eprintln!("[perpetua] terminating daemon (pid {})…", child.id());

            #[cfg(unix)]
            {
                unsafe {
                    libc::kill(child.id() as libc::pid_t, libc::SIGTERM);
                }
            }

            #[cfg(not(unix))]
            {
                let _ = child.kill();
            }

            // Reap the child to avoid zombies
            let _ = child.wait();
            eprintln!("[perpetua] daemon terminated.");
        }

        // Drop the child so subsequent calls are no-ops
        *guard = None;
    }
}
