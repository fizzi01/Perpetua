use ipc::{AtomicAsyncWriter};
use ipc::event::{CommandEvent, CommandType, EventParser, Parser};

#[tauri::command]
pub async fn choose_service(service: String, s: tauri::State<'_, AtomicAsyncWriter>) -> Result<(), String>
{
    let command = CommandEvent {
        command: CommandType::ServiceChoice,
        params: Some(serde_json::json!({ "service": service })),
    };
    let command = EventParser::serialize(&command).map_err(
        |e| format!("Failed to serialize choose_service command: {}", e)
    )?;
    s.send(command).await.map_err(|e| format!("Failed to send choose_service command: {}", e))?;
    Ok(())
}