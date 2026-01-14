import { invoke } from '@tauri-apps/api/core';
import { getType } from '../api/Utility';
import { CommandType } from '../api/Interface';

export function chooseService(service: 'client' | 'server'): Promise<void> {
    return invoke(getType(CommandType, CommandType.ServiceChoice), { service });
}

export function startServer(): Promise<void> {
    return invoke(getType(CommandType, CommandType.StartServer));
}

export function stopServer(): Promise<void> {
    return invoke(getType(CommandType, CommandType.StopServer));
}