import { invoke } from '@tauri-apps/api/core';
import { getType } from '../api/Utility';
import { CommandType, StreamType } from '../api/Interface';

export function chooseService(service: 'client' | 'server'): Promise<void> {
    return invoke(getType(CommandType, CommandType.ServiceChoice), { service });
}

export function startServer(): Promise<void> {
    return invoke(getType(CommandType, CommandType.StartServer));
}

export function stopServer(): Promise<void> {
    return invoke(getType(CommandType, CommandType.StopServer));
}

export function addClient(hostname: string, ip_address: string, screen_position: string): Promise<void> {
    return invoke(getType(CommandType, CommandType.AddClient), { hostname, ipAddress: ip_address, screenPosition: screen_position });
}

export function removeClient(hostname: string, ip_address: string): Promise<void> {
    return invoke(getType(CommandType, CommandType.RemoveClient), { hostname, ipAddress: ip_address });
}

export function enableStream(streamType: StreamType): Promise<void> {
    return invoke(getType(CommandType, CommandType.EnableStream), { streamType });
}

export function disableStream(streamType: StreamType): Promise<void> {
    return invoke(getType(CommandType, CommandType.DisableStream), { streamType });
}

export function saveServerConfig(host: string, port: number, sslEnabled: boolean): Promise<void> {
    return invoke(getType(CommandType, CommandType.SetServerConfig), { host, port, sslEnabled });
}

export function shareCertificate(timeout: number): Promise<void> {
    return invoke(getType(CommandType, CommandType.ShareCertificate), { timeout });
}

export function getStatus(): Promise<any> {
    return invoke(getType(CommandType, CommandType.Status));
}