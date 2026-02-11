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

use ipc::event::TypeToString;
use ipc::{EventType, NotificationEvent};
use tauri::{AppHandle, Emitter, Runtime};

pub trait Handable {
    fn handle<R: Runtime>(&self, app: &AppHandle<R>);
}

pub struct EventHandler {
    event: NotificationEvent,
}

impl EventHandler {
    pub fn new(event: NotificationEvent) -> Self {
        EventHandler { event }
    }
}

impl Handable for EventHandler {
    fn handle<R: Runtime>(&self, app: &AppHandle<R>) {
        match self.event.event_type {
            EventType::Pong => {
                // Silently ignore pong events
            }
            _ => match self.event.event_type.to_string() {
                Ok(event_type) => {
                    app.emit(&event_type, &self.event).unwrap_or_else(|e| {
                        println!("Failed to emit event ({:?})", e); // TODO: Should panic here?
                    });
                }
                Err(e) => {
                    println!("Failed to convert event type to string ({:?})", e);
                }
            },
        }
    }
}
