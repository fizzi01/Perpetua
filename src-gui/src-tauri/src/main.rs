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

use clap::Parser;
#[cfg(not(debug_assertions))]
use perpetua_lib::{DaemonConfig, DaemonProcess};

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
    /// Build a [`DaemonConfig`] from the CLI flags.
    #[cfg(not(debug_assertions))]
    fn daemon_config(&self) -> DaemonConfig {
        DaemonConfig {
            server: self.server,
            client: self.client,
            socket: self.socket.clone(),
            config_dir: self.config_dir.clone(),
            log_terminal: self.log_terminal,
            debug: self.debug,
        }
    }
}

fn main() {
    let cli = Cli::parse();

    #[cfg(not(debug_assertions))]
    {
        let config = cli.daemon_config();

        if cli.daemon_only {
            // --- daemon-only mode: no GUI, just handle the child -----------
            let daemon = DaemonProcess::spawn(&config);
            let daemon_for_hook = daemon.clone();

            ctrlc::set_handler(move || {
                eprintln!("[perpetua] shutting down daemon…");
                daemon_for_hook.terminate();
                std::process::exit(0);
            })
            .expect("Failed to set Ctrl-C handler");

            eprintln!("[perpetua] running in daemon-only mode. Press Ctrl-C to exit.");
            daemon.wait();
        } else {
            // --- GUI mode: daemon is spawned during Tauri setup -----------
            perpetua_lib::run(Some(config));
        }

        eprintln!("[perpetua] bye.");
    }

    #[cfg(debug_assertions)]
    {
        let _ = cli; // acknowledge parsed args in debug (no daemon to forward to)
        perpetua_lib::run(None);
    }
}
