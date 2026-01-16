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
                    app.emit(&event_type, &self.event).unwrap();
                }
                Err(e) => {
                    println!("Failed to convert event type to string ({:?})", e);
                }
            },
        }
    }
}
