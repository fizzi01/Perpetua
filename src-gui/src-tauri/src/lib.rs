#[cfg(target_os = "macos")]
use tauri::PhysicalPosition;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Manager, Position, Runtime, TitleBarStyle, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

use handler::{EventHandler, Handable};
use ipc::{
    connection::{connect, ConnectionError},
    AtomicAsyncWriter,
};
use ipc::{ConnectionHandler, DataListener};
use std::{sync::Mutex, time::Duration};

pub mod commands;
pub mod handler;

#[derive(Default)]
struct AppState {
    hard_close: bool,
}

fn force_close<R>(app: &AppHandle<R>)
where
    R: Runtime,
{
    let state = app.state::<Mutex<AppState>>();
    let mut state = state.lock().unwrap();
    state.hard_close = true;
    app.exit(0);
}

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

    force_close(app);
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
        .manage(Mutex::new(AppState { hard_close: false }))
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
            commands::shutdown,
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
                .inner_size(420.0, 600.0)
                .resizable(false);

            // Set macOS-specific window properties
            #[cfg(target_os = "macos")]
            let win_builder = win_builder
                .title_bar_style(TitleBarStyle::Overlay)
                .traffic_light_position(Position::Physical(PhysicalPosition { x: 30, y: 50 }));

            win_builder.build().unwrap();

            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&quit_i])?;

            TrayIconBuilder::new()
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "quit" => {
                        let handle = app.clone();
                        // Call shutdown command
                        tauri::async_runtime::spawn(async move {
                            let new = handle;
                            let cur_state = new.state::<AtomicAsyncWriter>();
                            let _ = commands::shutdown(cur_state).await;
                            force_close(&new);
                        });
                    }
                    _ => {
                        println!("menu item {:?} not handled", event.id);
                    }
                })
                .icon(app.default_window_icon().unwrap().clone())
                .show_menu_on_left_click(true)
                .build(app)?;

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application");

    app.run(|_app_handle, _e| match _e {
        tauri::RunEvent::ExitRequested { api, .. } => {
            let state = _app_handle.state::<Mutex<AppState>>();
            let state = state.lock().unwrap();
            if !state.hard_close {
                // Prevent the app from closing
                api.prevent_exit();
            }
        }
        _ => {}
    });
}
