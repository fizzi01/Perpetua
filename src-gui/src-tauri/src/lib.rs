#[cfg(target_os = "macos")]
use tauri::PhysicalPosition;
use tauri::{AppHandle, Manager, Position, Runtime, TitleBarStyle, WebviewUrl, WebviewWindowBuilder};

use std::time::Duration;
use ipc::{ConnectionHandler, DataListener};
use ipc::connection::{connect, ConnectionError};
use handler::{EventHandler, Handable};

pub mod commands;
pub mod handler;

async fn setup_connection<'a, R>(manager: AppHandle<R>) -> Result<(), ConnectionError>
where
    R: Runtime,
{
    let (r,w) = connect(Duration::from_millis(100)).await?;
    let c_w = w.get_writer().clone();

    // Clone the writer for use in commands
    manager.manage(c_w);

    let mut handler = ConnectionHandler::new(r,w);
    // Handle connection events here
    if let Err(e) = handler.listen(|msg| {
                EventHandler::new(msg).handle(&manager);
            }, &Duration::from_secs(1)).await 
    {
        //TODO: Handle listen error (it should close the application or try to reconnect)
        println!("Error listening to events: {:?}", e);
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            commands::service_choice, 
            commands::start_server, 
            commands::stop_server])
        .setup(|app| {
            let app_handle = app.handle().clone();
            // Initialize connection to the daemon
            tauri::async_runtime::spawn(async move {
                match setup_connection(app_handle).await {
                    Ok(_) => {  println!("Connection established"); },
                    Err(e) => println!("Failed to setup connection: {:?}", e),
                }
            });

            let win_builder =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::default())
                .title("Perpetua")
                .hidden_title(true)
                .title_bar_style(TitleBarStyle::Overlay)
                .inner_size(450.0, 600.0)
                .resizable(false);

            // Set macOS-specific window properties
            #[cfg(target_os = "macos")]
            let win_builder = win_builder
            .title_bar_style(TitleBarStyle::Overlay).traffic_light_position(Position::Physical(PhysicalPosition { x: 30, y: 50 }));

            win_builder.build().unwrap();

            // set background color only when building for macOS
            // #[cfg(target_os = "macos")]
            // {
            //     use objc2_app_kit::{NSColor, NSWindow};
            //     use objc2::rc::Retained;
            //     use objc2::runtime::AnyObject;

            //     let ns_window = window.ns_window().unwrap() as *mut AnyObject;
            //     let ns_window: Retained<NSWindow> = unsafe { Retained::retain(ns_window as *mut NSWindow).unwrap() };
                
            //     let bg_color = NSColor::colorWithRed_green_blue_alpha(
            //         15.0 / 255.0,
            //         23.0 / 255.0,
            //         42.0 / 255.0,
            //         1.0,
            //     );
            //     ns_window.setBackgroundColor(Some(&bg_color));
            // }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}


