use ipc::{AtomicAsyncWriter};
use ipc::event::{CommandEvent, CommandType, EventParser, Parser};

#[tauri::command]
pub async fn service_choice(service: String, s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String>
{
    let command = CommandEvent::build(
        CommandType::ServiceChoice, 
        &format!(r#"{{ "service": "{}" }}"#, service));
    let command = EventParser::serialize(&command).map_err(
        |e| format!("Failed to serialize {} command: {}", CommandType::ServiceChoice, e)
    )?;
    s.send(command).await.map_err(|e| format!("Failed to send {} command ({})", CommandType::ServiceChoice, e))?;
    Ok(())
}

#[tauri::command]
pub async fn start_server(s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String>
{
    let command = CommandEvent::build(
        CommandType::StartServer, 
        "{}");
    let command = EventParser::serialize(&command).map_err(
        |e| format!("Failed to serialize {} command: {}", CommandType::StartServer, e)
    )?;
    s.send(command).await.map_err(|e| format!("Failed to send {} command ({})", CommandType::StartServer, e))?;
    Ok(())
}

#[tauri::command]
pub async fn stop_server(s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String>
{
    let command = CommandEvent::build(
        CommandType::StopServer, 
        "{}");
    let command = EventParser::serialize(&command).map_err(
        |e| format!("Failed to serialize {} command: {}", CommandType::StopServer, e)
    )?;
    s.send(command).await.map_err(|e| format!("Failed to send {} command ({})", CommandType::StopServer, e))?;
    Ok(())
}

#[tauri::command]
pub async fn share_certificate(timeout: i32, s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String>
{
    let command = CommandEvent::build(
        CommandType::ShareCertificate, 
        &format!(r#"{{ "timeout": {} }}"#, timeout));
    let command = EventParser::serialize(&command).map_err(
        |e| format!("Failed to serialize {} command: {}", CommandType::ShareCertificate, e)
    )?;
    s.send(command).await.map_err(|e| format!("Failed to send {} command ({})", CommandType::ShareCertificate, e))?;
    Ok(())
}

#[tauri::command]
pub async fn status(s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String>
{
    let command = CommandEvent::build(
        CommandType::Status, 
        "{}");
    let command = EventParser::serialize(&command).map_err(
        |e| format!("Failed to serialize {} command: {}", CommandType::Status, e)
    )?;
    s.send(command).await.map_err(|e| format!("Failed to send {} command ({})", CommandType::Status, e))?;
    Ok(())
}