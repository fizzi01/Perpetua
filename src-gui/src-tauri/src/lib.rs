#[cfg(target_os = "macos")]
use tauri::PhysicalPosition;
use tauri::{TitleBarStyle, WebviewUrl, WebviewWindowBuilder, Position};

// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![greet])
        .setup(|app| {
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


