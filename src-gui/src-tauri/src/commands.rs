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

use ipc::event::{CommandEvent, CommandType, EventParser, Parser};
use ipc::log_reader::{get_log_file_path, read_all_lines, read_last_n_lines, LogResponse};
use ipc::AtomicAsyncWriter;
use local_ip_address::local_ip;

/**
 * Helper function to handle optional string parameters
 */
fn handle_string_param(param: String) -> String {
    if param.is_empty() {
        "null".to_string()
    } else {
        format!("\"{}\"", param)
    }
}

#[tauri::command]
pub async fn service_choice(
    service: String,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    let command = CommandEvent::build(
        CommandType::ServiceChoice,
        &format!(r#"{{ "service": {} }}"#, handle_string_param(service)),
    );
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::ServiceChoice,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::ServiceChoice,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn start_server(s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String> {
    let command = CommandEvent::build(CommandType::StartServer, "{}");
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::StartServer,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::StartServer,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn stop_server(s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String> {
    let command = CommandEvent::build(CommandType::StopServer, "{}");
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::StopServer,
            e
        )
    })?;
    s.send(command)
        .await
        .map_err(|e| format!("Failed to send {} command ({})", CommandType::StopServer, e))?;
    Ok(())
}

#[tauri::command]
pub async fn add_client(
    hostname: String,
    ip_addresses: Vec<String>,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    if hostname.is_empty() && ip_addresses.iter().all(|ip| ip.is_empty()) {
        return Err("Either hostname or ip address must be provided".to_string());
    }

    let ip_addresses_json = format!(
        "[{}]",
        ip_addresses
            .iter()
            .filter(|ip| !ip.is_empty())
            .map(|ip| format!("\"{}\"", ip))
            .collect::<Vec<_>>()
            .join(", ")
    );

    let command = CommandEvent::build(
        CommandType::AddClient,
        &format!(
            r#"{{ "hostname": {}, "ip_addresses": {} }}"#,
            handle_string_param(hostname),
            ip_addresses_json,
        ),
    );

    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::AddClient,
            e
        )
    })?;

    s.send(command)
        .await
        .map_err(|e| format!("Failed to send {} command ({})", CommandType::AddClient, e))?;
    Ok(())
}

#[tauri::command]
pub async fn approve_client(
    peer_ip: String,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    if peer_ip.is_empty() {
        return Err("peer_ip must be provided".to_string());
    }
    let command = CommandEvent::build(
        CommandType::ApproveClient,
        &format!(
            r#"{{ "peer_ip": {} }}"#,
            handle_string_param(peer_ip),
        ),
    );
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::ApproveClient,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::ApproveClient,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn set_client_layout(
    client_uid: Option<String>,
    hostname: Option<String>,
    ip_address: Option<String>,
    placements: serde_json::Value,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    let payload = serde_json::json!({
        "client_uid": client_uid,
        "hostname": hostname,
        "ip_address": ip_address,
        "placements": placements,
    });
    let command = CommandEvent::build(CommandType::SetClientLayout, &payload.to_string());
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::SetClientLayout,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::SetClientLayout,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn deny_client(
    peer_ip: String,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    if peer_ip.is_empty() {
        return Err("peer_ip must be provided".to_string());
    }
    let command = CommandEvent::build(
        CommandType::DenyClient,
        &format!(r#"{{ "peer_ip": {} }}"#, handle_string_param(peer_ip)),
    );
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::DenyClient,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::DenyClient,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn remove_client(
    client_uid: String,
    hostname: String,
    ip_address: String,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    if client_uid.is_empty() && hostname.is_empty() && ip_address.is_empty() {
        return Err("client_uid, hostname or ip address must be provided".to_string());
    }

    let command = CommandEvent::build(
        CommandType::RemoveClient,
        &format!(
            r#"{{ "client_uid": {}, "hostname": {}, "ip_address": {} }}"#,
            handle_string_param(client_uid),
            handle_string_param(hostname),
            handle_string_param(ip_address)
        ),
    );
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::RemoveClient,
            e
        )
    })?;

    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::RemoveClient,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn enable_stream(
    stream_type: i8,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    let command = CommandEvent::build(
        CommandType::EnableStream,
        &format!(r#"{{ "stream_type": {} }}"#, stream_type),
    );
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::EnableStream,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::EnableStream,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn disable_stream(
    stream_type: i8,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    let command = CommandEvent::build(
        CommandType::DisableStream,
        &format!(r#"{{ "stream_type": {} }}"#, stream_type),
    );
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::DisableStream,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::DisableStream,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn set_server_config(
    host: String,
    port: i32,
    ssl_enabled: bool,
    host_exclusive: bool,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    // Built with serde_json rather than string interpolation: `host` is
    // user-supplied, and a quote in it would previously produce a malformed
    // frame the daemon silently rejected.
    let params = serde_json::json!({
        "host": host,
        "port": port,
        "ssl_enabled": ssl_enabled,
        "host_exclusive": host_exclusive,
    });
    let command = CommandEvent::build(CommandType::SetServerConfig, &params.to_string());
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::SetServerConfig,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::SetServerConfig,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn share_certificate(
    timeout: i32,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    let command = CommandEvent::build(
        CommandType::ShareCertificate,
        &format!(r#"{{ "timeout": {} }}"#, timeout),
    );
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::ShareCertificate,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::ShareCertificate,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn start_client(s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String> {
    let command = CommandEvent::build(CommandType::StartClient, "{}");
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::StartClient,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::StartClient,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn stop_client(s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String> {
    let command = CommandEvent::build(CommandType::StopClient, "{}");
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::StopClient,
            e
        )
    })?;
    s.send(command)
        .await
        .map_err(|e| format!("Failed to send {} command ({})", CommandType::StopClient, e))?;
    Ok(())
}

#[tauri::command]
pub async fn request_pairing(s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String> {
    let command = CommandEvent::build(CommandType::RequestPairing, "{}");
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::RequestPairing,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::RequestPairing,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn set_otp(otp: String, s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String> {
    let command = CommandEvent::build(CommandType::SetOtp, &format!(r#"{{ "otp": "{}" }}"#, otp));
    let command = EventParser::serialize(&command)
        .map_err(|e| format!("Failed to serialize {} command: {}", CommandType::SetOtp, e))?;
    s.send(command)
        .await
        .map_err(|e| format!("Failed to send {} command ({})", CommandType::SetOtp, e))?;
    Ok(())
}

#[tauri::command]
pub async fn choose_server(
    uid: String,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    let command = CommandEvent::build(
        CommandType::ChooseServer,
        &format!(r#"{{ "uid": "{}" }}"#, uid),
    );
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::ChooseServer,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::ChooseServer,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn set_client_config(
    server_host: String,
    server_hostname: String,
    server_port: i32,
    ssl_enabled: bool,
    auto_reconnect: bool,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    let command = CommandEvent::build(
        CommandType::SetClientConfig,
        &format!(
            r#"{{ "server_host": "{}", "server_hostname": "{}", "server_port": {}, "ssl_enabled": {}, "auto_reconnect": {} }}"#,
            server_host, server_hostname, server_port, ssl_enabled, auto_reconnect
        ),
    );
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::SetClientConfig,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::SetClientConfig,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn status(s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String> {
    let command = CommandEvent::build(CommandType::Status, "{}");
    let command = EventParser::serialize(&command)
        .map_err(|e| format!("Failed to serialize {} command: {}", CommandType::Status, e))?;
    s.send(command)
        .await
        .map_err(|e| format!("Failed to send {} command ({})", CommandType::Status, e))?;
    Ok(())
}

#[tauri::command]
pub async fn shutdown(s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String> {
    let command = CommandEvent::build(CommandType::Shutdown, "{}");
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::Shutdown,
            e
        )
    })?;
    s.send(command)
        .await
        .map_err(|e| format!("Failed to send {} command ({})", CommandType::Shutdown, e))?;
    Ok(())
}

#[tauri::command]
pub async fn read_daemon_logs(num_lines: usize, all: bool) -> Result<LogResponse, String> {
    let log_file = get_log_file_path()?;

    let logs = if all {
        read_all_lines(&log_file).map_err(|e| format!("Failed to read log file: {}", e))?
    } else {
        let lines_to_read = if num_lines == 0 { 100 } else { num_lines };
        read_last_n_lines(&log_file, lines_to_read)
            .map_err(|e| format!("Failed to read log file: {}", e))?
    };

    Ok(LogResponse {
        total_lines: logs.len(),
        log_file: log_file.to_string_lossy().to_string(),
        logs,
    })
}

/// macOS pasteboard write, independent of WKWebView's user-activation rules.
#[tauri::command]
pub async fn copy_log_text(text: String) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        tauri::async_runtime::spawn_blocking(move || {
            use std::io::Write;
            use std::process::{Command, Stdio};

            // Pass log content as data over stdin, never through a shell.
            let mut child = Command::new("/usr/bin/pbcopy")
                .env("LC_ALL", "en_US.UTF-8")
                .stdin(Stdio::piped())
                .stdout(Stdio::null())
                .stderr(Stdio::piped())
                .spawn()
                .map_err(|e| format!("Failed to access clipboard: {}", e))?;
            let write_result = child.stdin.take()
                .ok_or_else(|| "Clipboard input unavailable".to_string())
                .and_then(|mut input| input.write_all(text.as_bytes()).map_err(|e| e.to_string()));
            // Closing stdin completes the pasteboard write; always reap the child.
            let output = child.wait_with_output().map_err(|e| e.to_string())?;
            write_result?;
            if !output.status.success() {
                return Err(format!("Clipboard write failed: {}", String::from_utf8_lossy(&output.stderr)));
            }
            Ok(())
        }).await.map_err(|e| e.to_string())?
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = text;
        Err("Native log clipboard is only used on macOS".to_string())
    }
}

/// Open only the daemon log resolved by the backend, never an arbitrary UI path.
#[tauri::command]
pub async fn open_daemon_log(app: tauri::AppHandle) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    let log_file = get_log_file_path()?;
    app.opener()
        .open_path(log_file.to_string_lossy().into_owned(), None::<&str>)
        .map_err(|e| format!("Failed to open log file: {}", e))
}

#[tauri::command]
pub async fn get_log_file_path_cmd() -> Result<String, String> {
    let log_file = get_log_file_path()?;
    Ok(log_file.to_string_lossy().to_string())
}

#[tauri::command]
pub async fn switch_tray_icon(_app: tauri::AppHandle, _active: bool) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        const ACTIVE_ICON: &[u8] = include_bytes!("../icons/macos/32x32.png");
        const IDLE_ICON: &[u8] = include_bytes!("../icons/macos/32x32_idle.png");
        let icon_data = if _active { ACTIVE_ICON } else { IDLE_ICON };
        if let Some(tray) = _app.tray_by_id("main") {
            let icon_data = tauri::image::Image::from_bytes(icon_data)
                .unwrap_or_else(|e| panic!("Failed to load icon from bytes: {}", e));
            if let Err(e) = tray.set_icon(Some(icon_data)) {
                eprintln!("Failed to set tray icon: {}", e);
            }
            if let Err(e) = tray.set_icon_as_template(true) {
                eprintln!("Failed to set tray icon as template: {}", e);
            }
        } else {
            eprintln!("Tray with ID 'main' not found");
        }
    }
    Ok(())
}

#[tauri::command]
pub async fn get_local_ip() -> Result<String, String> {
    local_ip()
        .map(|ip| ip.to_string())
        .map_err(|e| format!("Failed to get local IP address: {}", e))
}

#[tauri::command]
pub async fn get_autostart(s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String> {
    let command = CommandEvent::build(CommandType::GetAutostart, "{}");
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::GetAutostart,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::GetAutostart,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn set_autostart(
    mode: String,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    // Launch mode: "off" removes the entry; "server"/"client" make the app
    // auto-start that service at login; "plain" just launches minimized.
    let mode = match mode.as_str() {
        "off" | "server" | "client" | "plain" => mode,
        other => return Err(format!("invalid autostart mode: {}", other)),
    };

    // The daemon needs to know which executable to register. We resolve it
    // here in the GUI process because the daemon runs detached and can't
    // necessarily introspect us (think systemd user service).
    let current_exe = std::env::current_exe()
        .map_err(|e| format!("Failed to determine GUI executable path: {}", e))?;

    // On macOS, current_exe() points at the Mach-O *inside* the bundle
    // (Perpetua.app/Contents/MacOS/Perpetua). Registering that inner binary
    // makes launchd bypass LaunchServices and spawn a second, bundle-less dock
    // icon. Walk up to the enclosing `.app` bundle and register that instead so
    // LaunchServices reuses the single dock icon and dedupes instances.
    #[cfg(target_os = "macos")]
    let exec_path = current_exe
        .ancestors()
        .find(|p| p.extension().is_some_and(|ext| ext == "app"))
        .unwrap_or(current_exe.as_path())
        .to_string_lossy()
        .to_string();

    #[cfg(not(target_os = "macos"))]
    let exec_path = current_exe.to_string_lossy().to_string();

    let params = format!(
        r#"{{ "mode": "{}", "exec_path": "{}" }}"#,
        mode,
        exec_path.replace('\\', r"\\").replace('"', r#"\""#)
    );
    let command = CommandEvent::build(CommandType::SetAutostart, &params);
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::SetAutostart,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::SetAutostart,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn get_permissions(s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String> {
    let command = CommandEvent::build(CommandType::GetPermissions, "{}");
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::GetPermissions,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::GetPermissions,
            e
        )
    })?;
    Ok(())
}

/// Ask the daemon which local interfaces exist.
///
/// The daemon is the only authority on addresses - the frontend must not run
/// its own route lookup, which is how the wrong interface used to end up
/// persisted as the server's advertise address on multi-homed machines.
#[tauri::command]
pub async fn list_network_interfaces(
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    let command = CommandEvent::build(CommandType::ListNetworkInterfaces, "{}");
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::ListNetworkInterfaces,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::ListNetworkInterfaces,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn request_permissions(
    permission_type: Option<String>,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    // ``permission_type`` is an optional specific permission (e.g.
    // "accessibility"); when absent the daemon requests every missing one.
    let params = match permission_type {
        Some(t) => format!(
            r#"{{ "type": "{}" }}"#,
            t.replace('\\', r"\\").replace('"', r#"\""#)
        ),
        None => "{}".to_string(),
    };
    let command = CommandEvent::build(CommandType::RequestPermissions, &params);
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::RequestPermissions,
            e
        )
    })?;
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::RequestPermissions,
            e
        )
    })?;
    Ok(())
}
