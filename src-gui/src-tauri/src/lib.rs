#[cfg(target_os = "macos")]
use tauri::PhysicalPosition;
use tauri::{
    AppHandle, Manager, Position, Runtime, TitleBarStyle, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

use handler::{EventHandler, Handable};
use ipc::connection::{connect, ConnectionError};
use ipc::{ConnectionHandler, DataListener};
use std::time::Duration;

pub mod commands;
pub mod handler;

fn handle_critical<R>(title: &str, error: &str, app: &AppHandle<R>)
where
    R: Runtime,
{
    app.dialog()
        .message(&format!(
            "A critical error occurred.\n{}\nThe application will now close.",
            error
        ))
        .kind(MessageDialogKind::Error)
        .title(title)
        .blocking_show();
    app.exit(0);
}

async fn setup_connection<'a, R>(manager: AppHandle<R>) -> Result<(), ConnectionError>
where
    R: Runtime,
{
    let (r, w) = match connect(Duration::from_millis(100)).await {
        Ok(conn) => conn,
        Err(e) => {
            println!("Error connecting to daemon: {:?}", e);
            handle_critical("Service unavailable", "", &manager);
            return Err(e);
        }
    };

    let c_w = w.get_writer().clone();

    // Clone the writer for use in commands
    manager.manage(c_w);

    let mut handler = ConnectionHandler::new(r, w);
    // Handle connection events here
    if let Err(e) = handler
        .listen(
            |msg| {
                EventHandler::new(msg).handle(&manager);
            },
            &Duration::from_secs(1),
        )
        .await
    {
        println!("Error listening to events: {:?}", e);
        // Connection lost, close the app
        handle_critical("Service Disconnected", "", &manager);
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            // -- Server Commands --
            commands::start_server,
            commands::stop_server,
            commands::share_certificate,
            commands::add_client,
            commands::remove_client,
            commands::set_server_config,
            // -- General Commands --
            commands::status,
            commands::service_choice,
            // -- Stream Commands --
            commands::enable_stream,
            commands::disable_stream,
        ])
        .setup(|app| {
            let app_handle = app.handle().clone();
            // Initialize connection to the daemon
            tauri::async_runtime::spawn(async move { setup_connection(app_handle).await });

            let win_builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::default())
                .title("Perpetua")
                .hidden_title(true)
                .title_bar_style(TitleBarStyle::Overlay)
                .inner_size(450.0, 600.0)
                .resizable(false);

            // Set macOS-specific window properties
            #[cfg(target_os = "macos")]
            let win_builder = win_builder
                .title_bar_style(TitleBarStyle::Overlay)
                .traffic_light_position(Position::Physical(PhysicalPosition { x: 30, y: 50 }));

            win_builder.build().unwrap();

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application");

    app.run(|_app_handle, _e| match _e {
        // tauri::RunEvent::ExitRequested { api, .. } => {
        //     // Prevent the app from closing immediately
        //     api.prevent_exit();
        //     let app_handle = app_handle.clone();
        //     // Perform any cleanup or finalization here before exiting
        //     tauri::async_runtime::spawn(async move {
        //         // Add any necessary cleanup code here
        //         // For example, notify the daemon about shutdown
        //         // Then exit the app
        //         app_handle.exit(0);
        //     });
        // }
        _ => {}
    });
}
