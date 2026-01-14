import { listen} from '@tauri-apps/api/event';
import { CommandPayload, EventType, CommandType, GeneralEvent } from '../api/Interface';
import { getType } from './Utility';

/**
 * Listen for a specific event with a command payload.
 * @param eventType The type of event to listen for.
 * @param command The command type to filter the events.
 * @param callback The callback function to execute when the event is received.
 */
export function listenCommand(eventType: EventType, command: CommandType, callback: (data: CommandPayload) => void): Promise<() => void> {
    let event = getType(EventType, eventType);
    return listen<CommandPayload>(event, (event => {
        console.log('Command received', event);
        let payload = event.payload;
        let data = payload?.data;
        let message = payload?.message;
        if (data && message) {
            if (data?.command == getType(CommandType, command)) {
                callback(payload);
            }
        } else {
            console.warn('Invalid payload structure', payload);
        }
    }));
}

/**
 * Listen for a general event.
 * @param eventType The type of event to listen for.
 * @param callback The callback function to execute when the event is received.
 */
export function listenGeneralEvent(eventType: EventType, callback: (data: GeneralEvent) => void): Promise<() => void> {
    let event = getType(EventType, eventType);
    return listen<GeneralEvent>(event, (event => {
        console.log('General event received', event);
        let payload = event.payload;
        let data = payload?.data;
        let message = payload?.message;
        if (data || message) {
            callback(payload);
        } else {
            console.warn('Invalid payload structure', payload);
        }
    }));
}