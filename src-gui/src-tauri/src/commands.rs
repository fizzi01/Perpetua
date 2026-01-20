use ipc::event::{CommandEvent, CommandType, EventParser, Parser};
use ipc::AtomicAsyncWriter;
use ipc::log_reader::{LogResponse, get_log_file_path, read_all_lines, read_last_n_lines};

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
    ip_address: String,
    screen_position: String,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    if hostname.is_empty() && ip_address.is_empty() {
        return Err("Either hostname or ip address must be provided".to_string());
    }

    if screen_position.is_empty() {
        return Err("Screen position must be provided".to_string());
    }

    let command = CommandEvent::build(
        CommandType::AddClient,
        &format!(
            r#"{{ "hostname": {}, "ip_address": {}, "screen_position": {} }}"#,
            handle_string_param(hostname),
            handle_string_param(ip_address),
            handle_string_param(screen_position)
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
pub async fn remove_client(
    hostname: String,
    ip_address: String,
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    if hostname.is_empty() && ip_address.is_empty() {
        return Err("Either hostname or ip address must be provided".to_string());
    }

    let command = CommandEvent::build(
        CommandType::RemoveClient,
        &format!(
            r#"{{ "hostname": {}, "ip_address": {} }}"#,
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
    s: tauri::State<'_, AtomicAsyncWriter>,
) -> Result<(), String> {
    let command = CommandEvent::build(
        CommandType::SetServerConfig,
        &format!(
            r#"{{ "host": "{}", "port": {}, "ssl_enabled": {} }}"#,
            host, port, ssl_enabled
        ),
    );
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
    s.send(command).await.map_err(|e| {
        format!(
            "Failed to send {} command ({})",
            CommandType::StopClient,
            e
        )
    })?;
    Ok(())
}

#[tauri::command]
pub async fn set_otp(otp: String, s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String> {
    let command = CommandEvent::build(
        CommandType::SetOtp,
        &format!(r#"{{ "otp": "{}" }}"#, otp),
    );
    let command = EventParser::serialize(&command).map_err(|e| {
        format!(
            "Failed to serialize {} command: {}",
            CommandType::SetOtp,
            e
        )
    })?;
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
    s.send(command)
        .await
        .map_err(|e| format!("Failed to send {} command ({})", CommandType::ChooseServer, e))?;
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
        &format!(r#"{{ "server_host": "{}", "server_hostname": "{}", "server_port": {}, "ssl_enabled": {}, "auto_reconnect": {} }}"#,
            server_host, server_hostname, server_port, ssl_enabled, auto_reconnect)
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

#[tauri::command]
pub async fn get_log_file_path_cmd() -> Result<String, String> {
    let log_file = get_log_file_path()?;
    Ok(log_file.to_string_lossy().to_string())
}
