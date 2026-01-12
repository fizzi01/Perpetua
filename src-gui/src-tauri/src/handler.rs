use tauri::{AppHandle,Emitter, Runtime};
use ipc::{EventType, NotificationEvent};

pub trait Handable{
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
            EventType::CommandSuccess => {
                // Forward to the frontend
                app.emit("command-success", &self.event).unwrap();
            }
            EventType::CommandError => {
                // Forward to the frontend
                app.emit("command-error", &self.event).unwrap();
            }
            _ => {
                // Do nothing
            }
        }
    }
}