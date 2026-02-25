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

use std::process::Child;
use std::sync::{Arc, Mutex};

/// Handle to the daemon child process, stored as Tauri managed state.
///
/// In release builds `main.rs` spawns the daemon and passes the [`Child`] here.
/// In debug builds (or daemon-only mode) use [`DaemonProcess::empty`].
#[derive(Clone)]
pub struct DaemonProcess {
    inner: Arc<Mutex<Option<Child>>>,
}

impl DaemonProcess {
    /// Wrap an existing daemon [`Child`].
    pub fn new(child: Child) -> Self {
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
