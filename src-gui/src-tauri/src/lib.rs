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

#[cfg(target_os = "macos")]
use tauri::{PhysicalPosition, Position, TitleBarStyle};

use tauri::{
    menu::{MenuBuilder, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, Runtime, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
// use tauri_plugin_positioner::WindowExt;

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
    connected: bool,
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
    // Create the splashscreen window
    // if let Err(e) = create_splashscreen_window(&manager) {
    //     println!("Error during window creation {:?}", e);
    //     handle_critical("Critical error on startup", "", &manager);
    // }
    show_window(&manager, "splashscreen");

    let (r, w) = match connect(Duration::from_millis(100), Duration::from_secs(5)).await {
        Ok(conn) => {
            {
                let state = manager.state::<Mutex<AppState>>();
                let mut state = state.lock().unwrap();
                state.connected = true
            }

            conn
        }
        Err(e) => {
            println!("Error connecting to daemon: {:?}", e);
            handle_critical("Service unavailable", "", &manager);
            return Err(e);
        }
    };

    let c_w = w.get_writer().clone();

    // Clone the writer for use in commands
    manager.manage(c_w);

    // Close the splash screen and open the main window after manager is set up
    let splash_window = manager.get_webview_window("splashscreen").unwrap();
    splash_window.close().unwrap();
    if let Err(e) = create_main_window(&manager) {
        println!("Error during window creation {:?}", e);
        handle_critical("Critical error on startup", "", &manager);
    }
    show_window(&manager, "main");

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

#[cfg(debug_assertions)]
fn prevent_default_ctxmenu() -> tauri::plugin::TauriPlugin<tauri::Wry> {
    tauri_plugin_prevent_default::debug()
}

#[cfg(not(debug_assertions))]
fn prevent_default_ctxmenu() -> tauri::plugin::TauriPlugin<tauri::Wry> {
    use tauri_plugin_prevent_default::Flags;

    tauri_plugin_prevent_default::Builder::new()
        .with_flags(Flags::all())
        .build()
}

fn create_main_window<R>(app: &AppHandle<R>) -> Result<(), Box<dyn std::error::Error>>
where
    R: Runtime,
{
    let mut win_builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::default())
        .title("Perpetua")
        .inner_size(435.0, 600.0)
        .resizable(false)
        .visible(false).center();

    // Set macOS-specific window properties
    #[cfg(target_os = "macos")]
    {
        win_builder = win_builder
            .hidden_title(true)
            .title_bar_style(TitleBarStyle::Overlay)
            .traffic_light_position(Position::Physical(PhysicalPosition { x: 30, y: 50 }));
    }

    #[cfg(target_os = "windows")]
    {
        win_builder = win_builder.decorations(false).transparent(true);
    }

    win_builder.build().unwrap();

    let show = MenuItem::with_id(app, "show_window", "Show", true, None::<&str>)?;
    let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let mut menu = MenuBuilder::new(app);

    menu = menu.item(&show);

    // #[cfg(debug_assertions)]
    menu = menu.item(&MenuItem::with_id(
        app,
        "show_log",
        "Show Logs",
        true,
        None::<&str>,
    )?);
    let menu = menu.separator().item(&quit_i).build()?;

    let tray = TrayIconBuilder::with_id("main")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show_window" => {
                show_window(app, "main");
            }
            "show_log" => {
                let app_handle = app.clone();
                app_handle.emit("show_log", {}).unwrap();

                show_window(app, "main");
            }
            "quit" => {
                let state = app.state::<Mutex<AppState>>();
                let state = state.lock().unwrap();
                if state.connected{
                    let handle = app.clone();
                    // Call shutdown command only if connected
                    tauri::async_runtime::spawn(async move {
                        let new = handle;
                        let cur_state = new.state::<AtomicAsyncWriter>();
                        let _ = commands::shutdown(cur_state).await;
                        force_close(&new);
                    });
                } else {
                    // Just close
                    force_close(&app);
                }
            }
            _ => {
                println!("menu item {:?} not handled", event.id);
            }
        });

    #[allow(unused)]
    let mut icon_data = app.default_window_icon().unwrap().clone();
    
    #[cfg(target_os = "macos")]
    {
        icon_data = tauri::image::Image::from_bytes(include_bytes!("../icons/macos/32x32_idle.png"))?;
    }
    
    let mut tray = tray.icon(icon_data);

    #[cfg(target_os = "macos")]
    {
        tray = tray.icon_as_template(true)
    }

    tray.show_menu_on_left_click(true).build(app)?;

    Ok(())
}

#[allow(dead_code)]
fn create_splashscreen_window<R>(app: &AppHandle<R>) -> Result<(), Box<dyn std::error::Error>>
where
    R: Runtime,
{
    let mut splashscreen_win_builder =
        WebviewWindowBuilder::new(app, "splashscreen", WebviewUrl::App("splashscreen".into()))
            .title("Perpetua")
            .inner_size(300.0, 200.0)
            .resizable(false)
            .visible(true).center();

    #[cfg(target_os = "macos")]
    {
        splashscreen_win_builder = splashscreen_win_builder
            .hidden_title(true)
            .decorations(false)
            .title_bar_style(TitleBarStyle::Transparent);
    }

    #[cfg(target_os = "windows")]
    {
        splashscreen_win_builder = splashscreen_win_builder
            .decorations(false)
            .transparent(true);
    }

    splashscreen_win_builder.build()?;
    // let _ = win
    //     .as_ref()
    //     .window()
    //     .move_window(tauri_plugin_positioner::Position::Center);
    // win.show()?;

    Ok(())
}

fn show_window<R>(app: &AppHandle<R>, label: &str)
where
    R: Runtime,
{
    let window = app.get_webview_window(label).unwrap();
    // let _ = window
    //     .as_ref()
    //     .window()
    //     .move_window(tauri_plugin_positioner::Position::Center);
    window.show().unwrap();
    window.set_focus().unwrap();

    #[cfg(target_os = "macos")]
    app.set_activation_policy(tauri::ActivationPolicy::Regular)
        .unwrap_or(());
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut app = tauri::Builder::default();

    #[cfg(desktop)]
    {
        app = app.plugin(tauri_plugin_single_instance::init(|app, _, _| {
            let _ = app
                .get_webview_window("main")
                .expect("no main window")
                .set_focus();
        }))
    }

    app = app
        // .plugin(tauri_plugin_positioner::init())
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(prevent_default_ctxmenu())
        .manage(Mutex::new(AppState {
            hard_close: false,
            connected: false,
        }))
        .invoke_handler(tauri::generate_handler![
            // -- Server Commands --
            commands::start_server,
            commands::stop_server,
            commands::share_certificate,
            commands::add_client,
            commands::remove_client,
            commands::set_server_config,
            // -- Client Commands --
            commands::start_client,
            commands::stop_client,
            commands::set_otp,
            commands::choose_server,
            commands::set_client_config,
            // -- General Commands --
            commands::status,
            commands::service_choice,
            commands::shutdown,
            // -- Stream Commands --
            commands::enable_stream,
            commands::disable_stream,
            // -- Log Commands --
            commands::read_daemon_logs,
            commands::get_log_file_path_cmd,
            // -- UI Commands --
            commands::switch_tray_icon,
        ])
        .setup(|app| {
            // Initialize connection to the daemon
            tauri::async_runtime::spawn(setup_connection(app.handle().clone()));
            Ok(())
        })
        .on_window_event(|window, event| match event {
            tauri::WindowEvent::CloseRequested { api, .. } => {
                let app_handle = window.app_handle();
                let state = app_handle.state::<Mutex<AppState>>();
                let state = state.lock().unwrap();
                if !state.hard_close {
                    // Prevent the window from closing
                    api.prevent_close();
                    window.hide().unwrap();

                    #[cfg(target_os = "macos")]
                    app_handle
                        .set_activation_policy(tauri::ActivationPolicy::Accessory)
                        .unwrap_or(());
                }
            }
            _ => {}
        });

    let app = app
        .build(tauri::generate_context!())
        .expect("error while running tauri application");

    app.run(|_app_handle, _e| match _e {
        tauri::RunEvent::ExitRequested { api, .. } => {
            let state = _app_handle.state::<Mutex<AppState>>();
            let state = state.lock().unwrap();
            if !state.hard_close {
                // Prevent the app from closing
                api.prevent_exit();

                #[cfg(target_os = "macos")]
                {
                    let app_handle = _app_handle.clone();
                    app_handle
                        .set_activation_policy(tauri::ActivationPolicy::Accessory)
                        .unwrap_or(());
                }
            }
        }
        #[cfg(any(target_os = "macos", debug_assertions))]
        tauri::RunEvent::Exit => {
            let state = _app_handle.state::<Mutex<AppState>>();
            let state = state.lock().unwrap();
            if !state.hard_close && state.connected{
                // Only if connected
                // With hard_close, a shutdown command has already been sent
                let app_handle = _app_handle.clone();
                tauri::async_runtime::spawn(async move {
                    let cur_state = app_handle.state::<AtomicAsyncWriter>();
                    let _ = commands::shutdown(cur_state).await;
                });
            }
        }
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Reopen {
            has_visible_windows,
            ..
        } => {
            let state = _app_handle.state::<Mutex<AppState>>();
            let state = state.lock().unwrap();
            if !has_visible_windows && state.connected {
                let window = _app_handle.get_webview_window("main").unwrap();
                window.show().unwrap();
                window.set_focus().unwrap();
            }
        }
        _ => {}
    });
}
