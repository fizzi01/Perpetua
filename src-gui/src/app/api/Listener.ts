import { listen} from '@tauri-apps/api/event';
import { CommandPayload, EventType, CommandType, GeneralEvent } from '../api/Interface';
import { getType, getEnumValue } from './Utility';

/**
 * Listen for a specific event with a command payload.
 * If the command parameter is an array, it will match any of the commands in the array.
 * 
 * @param eventType The type of event to listen for.
 * @param command The command or commands to filter for.
 * @param callback The callback function to execute when the event is received.
 */
export function listenCommand(eventType: EventType, command: CommandType | CommandType[], callback: (data: CommandPayload, command: CommandType) => void): Promise<() => void> {
    let event = getType(EventType, eventType);
    return listen<CommandPayload>(event, (event => {
        let payload = event.payload;
        let data = payload?.data;
        let message = payload?.message;
        if (data && message && data?.command) {
            if (Array.isArray(command)) {
                if (command.some(cmd => data?.command == getType(CommandType, cmd))) {
                    callback(payload, getEnumValue(CommandType, data?.command) as CommandType);
                }
            } else if (data?.command == getType(CommandType, command)) {
                callback(payload, getEnumValue(CommandType, data?.command) as CommandType);
            }
        } else {
            console.warn('Invalid payload structure', payload);
        }
    }));
}

/**
 * Listen for a general event.
 * @param eventType The type of event to listen for.
 * @param no_data If true, the callback will be executed even if there is no data or message in the payload.
 * @param callback The callback function to execute when the event is received.
 */
export function listenGeneralEvent(eventType: EventType, no_data: boolean = false, callback: (data: GeneralEvent) => void): Promise<() => void> {
    let event = getType(EventType, eventType);
    return listen<GeneralEvent>(event, (event => {
        console.log('General event received', event);
        let payload = event.payload;
        let data = payload?.data;
        let message = payload?.message;
        if (no_data) {
            callback(payload);
        } else if (data || message) {
            callback(payload);
        } else {
            console.warn('Invalid payload structure', payload);
        }
    }));
}