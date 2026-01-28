/*
 * Perpatua - open-source and cross-platform KVM software.
 * Copyright (c) 2026 Federico Izzi.
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 *
 */

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