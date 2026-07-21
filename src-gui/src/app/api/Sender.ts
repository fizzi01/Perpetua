/*
 * Perpetua - open-source and cross-platform KVM software.
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

import {invoke} from '@tauri-apps/api/core';
import {getType} from '../api/Utility';
import {CommandType, MonitorPlacement, StreamType} from '../api/Interface';

// -- SERVER-SPECIFIC API CALLS --

export function startServer(): Promise<void> {
    return invoke(getType(CommandType, CommandType.StartServer));
}

export function stopServer(): Promise<void> {
    return invoke(getType(CommandType, CommandType.StopServer));
}

export function addClient(hostname: string, ip_addresses: string[]): Promise<void> {
    return invoke(getType(CommandType, CommandType.AddClient), {
        hostname,
        ipAddresses: ip_addresses,
    });
}

export function removeClient(hostname: string, ip_address: string): Promise<void> {
    return invoke(getType(CommandType, CommandType.RemoveClient), {hostname, ipAddress: ip_address});
}

export function approveClient(peer_ip: string): Promise<void> {
    return invoke(getType(CommandType, CommandType.ApproveClient), {
        peerIp: peer_ip,
    });
}

export function denyClient(peer_ip: string): Promise<void> {
    return invoke(getType(CommandType, CommandType.DenyClient), {peerIp: peer_ip});
}

/** Persist a client's workspace placements. UID is preferred; hostname/IP fallbacks for unpaired clients. */
export function setClientLayout(
    clientUid: string | undefined,
    placements: MonitorPlacement[],
    extra: {hostname?: string; ipAddress?: string} = {},
): Promise<void> {
    return invoke(getType(CommandType, CommandType.SetClientLayout), {
        clientUid,
        hostname: extra.hostname,
        ipAddress: extra.ipAddress,
        // Snake-case keys match the daemon's expected dict shape.
        placements: placements.map((p) => ({
            client_monitor_id: p.client_monitor_id,
            workspace_x: p.workspace_x,
            workspace_y: p.workspace_y,
            width: p.width,
            height: p.height,
        })),
    });
}

export function saveServerConfig(host: string, port: number, sslEnabled: boolean): Promise<void> {
    return invoke(getType(CommandType, CommandType.SetServerConfig), {host, port, sslEnabled});
}

export function shareCertificate(timeout: number): Promise<void> {
    return invoke(getType(CommandType, CommandType.ShareCertificate), {timeout});
}

// -- CLIENT-SPECIFIC API CALLS --

export function startClient(): Promise<void> {
    return invoke(getType(CommandType, CommandType.StartClient));
}

export function stopClient(): Promise<void> {
    return invoke(getType(CommandType, CommandType.StopClient));
}

export function setOtp(otp: string): Promise<void> {
    return invoke(getType(CommandType, CommandType.SetOtp), {otp});
}

export function requestPairing(): Promise<void> {
    return invoke(getType(CommandType, CommandType.RequestPairing));
}

export function chooseServer(uid: string): Promise<void> {
    return invoke(getType(CommandType, CommandType.ChooseServer), {uid});
}

export function saveClientConfig(serverHost: string, serverHostname: string, serverPort: number, sslEnabled: boolean, autoReconnect: boolean): Promise<void> {
    return invoke(getType(CommandType, CommandType.SetClientConfig), {
        serverHost,
        serverHostname,
        serverPort,
        sslEnabled,
        autoReconnect
    });
}

// -- GENERAL API CALLS --

export function chooseService(service: 'client' | 'server'): Promise<void> {
    console.log(`[Sender] Choosing service: ${service}`);
    return invoke(getType(CommandType, CommandType.ServiceChoice), {service});
}

export function getStatus(): Promise<any> {
    return invoke(getType(CommandType, CommandType.Status));
}

export function enableStream(streamType: StreamType): Promise<void> {
    return invoke(getType(CommandType, CommandType.EnableStream), {streamType});
}

export function disableStream(streamType: StreamType): Promise<void> {
    return invoke(getType(CommandType, CommandType.DisableStream), {streamType});
}

export function switchTrayIcon(active: boolean): void {
    try {
        invoke("switch_tray_icon", {active});
    } catch (error) {
        console.error("Failed to switch tray icon:", error);
    }
}

export function getLocalIpAddress(): Promise<string> {
    return invoke("get_local_ip");
}

// -- OS-LEVEL PERMISSIONS (macOS gate) --

export function getPermissions(): Promise<void> {
    return invoke(getType(CommandType, CommandType.GetPermissions));
}

/**
 * Trigger the OS permission prompt / open System Settings.
 * @param permissionType Optional specific permission (e.g. "accessibility");
 *                        when omitted the daemon requests every missing one.
 */
export function requestPermissions(permissionType?: string): Promise<void> {
    return invoke(getType(CommandType, CommandType.RequestPermissions), {permissionType});
}