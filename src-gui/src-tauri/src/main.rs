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

// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(not(debug_assertions))]
use std::env;
#[cfg(not(debug_assertions))]
use std::io::{BufRead, BufReader};
#[cfg(not(debug_assertions))]
use std::path::PathBuf;
#[cfg(not(debug_assertions))]
use std::process::{Child, Command, Stdio};
#[cfg(not(debug_assertions))]
use std::sync::{Arc, Mutex};
#[cfg(not(debug_assertions))]
use std::thread;

use clap::Parser;
use perpetua_lib::DaemonProcess;

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

#[derive(Parser, Debug)]
#[command(name = "Perpetua", version, about, long_about = None)]
struct Cli {
    /// Run daemon only (no GUI)
    #[arg(short = 'd', long = "daemon")]
    daemon_only: bool,

    /// Start server service
    #[arg(short, long, group = "service")]
    server: bool,

    /// Start client service
    #[arg(short, long, group = "service")]
    client: bool,

    /// Socket path (Unix socket) or host:port (TCP on Windows)
    #[arg(long)]
    socket: Option<String>,

    /// Configuration directory path
    #[arg(long)]
    config_dir: Option<String>,

    /// Log daemon output to terminal
    #[arg(long)]
    log_terminal: bool,

    /// Enable debug mode
    #[arg(long)]
    debug: bool,
}

impl Cli {
    /// Build the argument list forwarded to the Python daemon.
    ///
    /// GUI-only flags (`-d`/`--daemon`) are excluded — the daemon
    /// doesn't know about them.
    #[cfg(not(debug_assertions))]
    fn daemon_args(&self) -> Vec<String> {
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
#[cfg(not(debug_assertions))]
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

#[cfg(not(debug_assertions))]
fn spawn_daemon(cli: &Cli) -> Child {
    let exe = daemon_executable_path();
    if !exe.exists() {
        eprintln!("[perpetua] daemon executable not found: {}", exe.display());
        std::process::exit(1);
    }

    let mut cmd = Command::new(&exe);
    cmd.args(cli.daemon_args());

    if cli.log_terminal {
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());
    } else {
        cmd.stdout(Stdio::null());
        cmd.stderr(Stdio::null());
    }

    // stdin is always inherited so the daemon can read from terminal if needed
    cmd.stdin(Stdio::inherit());

    cmd.spawn().unwrap_or_else(|e| {
        eprintln!(
            "[perpetua] failed to spawn daemon ({}): {}",
            exe.display(),
            e
        );
        std::process::exit(1);
    })
}

/// Forward a piped stream line-by-line to stdout.
#[cfg(not(debug_assertions))]
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

fn main() {
    let cli = Cli::parse();

    #[cfg(not(debug_assertions))]
    {
        // --- spawn daemon -------------------------------------------------
        let mut daemon = spawn_daemon(&cli);
        let daemon_pid = daemon.id();
        eprintln!("[perpetua] daemon spawned (pid {})", daemon_pid);

        // Forward piped streams in background threads
        let _stdout_handle = daemon.stdout.take().map(|s| forward_stream(s, ""));
        let _stderr_handle = daemon.stderr.take().map(|s| forward_stream(s, ""));

        if cli.daemon_only {
            // --- daemon-only mode: no GUI, just handle the child ---------
            let daemon = Arc::new(Mutex::new(Some(daemon)));
            let daemon_for_hook = Arc::clone(&daemon);

            ctrlc::set_handler(move || {
                if let Ok(mut guard) = daemon_for_hook.lock() {
                    if let Some(ref mut child) = *guard {
                        eprintln!("[perpetua] shutting down daemon…");
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
                std::process::exit(0);
            })
            .expect("Failed to set Ctrl-C handler");

            eprintln!("[perpetua] running in daemon-only mode. Press Ctrl-C to exit.");
            if let Ok(mut guard) = daemon.lock() {
                if let Some(ref mut child) = *guard {
                    let _ = child.wait();
                }
            };
        } else {
            // DaemonProcess is managed as Tauri state
            let daemon_handle = DaemonProcess::new(daemon);
            perpetua_lib::run(daemon_handle);
        }

        eprintln!("[perpetua] bye.");
    }

    #[cfg(debug_assertions)]
    {
        let _ = cli; // acknowledge parsed args in debug (no daemon to forward to)
        perpetua_lib::run(DaemonProcess::empty());
    }
}
