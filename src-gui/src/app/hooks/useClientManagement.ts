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

import {useCallback, useState} from 'react';
import {ClientObj, MonitorInfo, MonitorPlacement} from '../api/Interface';

interface Client {
    id: string;
    uid?: string;
    name: string;
    ips?: string[];
    status: 'online' | 'offline';
    connectedAt?: Date;
    monitors?: MonitorInfo[];
    placements?: MonitorPlacement[];
}

export function isPlaced(client: {placements?: MonitorPlacement[]}): boolean {
    return (client.placements?.length ?? 0) > 0;
}

/**
 * Hook to manage client state consistently
 * Uses robust matching logic based on uid -> ip -> name
 */
export function useClientManagement() {
    const [clients, setClients] = useState<Client[]>([]);

    // Calculate connected count directly from clients to avoid sync issues
    const connectedCount = clients.filter(c => c.status === 'online').length;

    /**
     * Find an existing client using cascading match logic:
     * 1. If the incoming client has a UID, match ONLY by UID. IP/hostname
     *    fallback is restricted to records that have no UID yet (upgrading a
     *    still-anonymous entry) - never merge into a record with a different
     *    UID, which would clobber a distinct client that merely shares an IP
     *    or hostname (e.g. two machines behind the same NAT).
     * 2. If it has no UID, fall back to IP, then hostname (legacy).
     */
    const findExistingClient = useCallback((clientData: ClientObj, clientList: Client[]) => {
        if (clientData.uid) {
            const byUid = clientList.find(c => c.uid === clientData.uid);
            if (byUid) return byUid;

            // UID present but unknown: only adopt a still-anonymous record
            // (no UID) that overlaps by IP/hostname; skip records that carry a
            // different UID.
            const anon = (c: Client) => !c.uid;
            if (clientData.ip_addresses && clientData.ip_addresses.length > 0) {
                const byIp = clientList.find(c => anon(c) && c.ips && clientData.ip_addresses.some(ip => c.ips!.includes(ip)));
                if (byIp) return byIp;
            }
            if (clientData.host_name) {
                const byName = clientList.find(c => anon(c) && c.name === clientData.host_name);
                if (byName) return byName;
            }
            return undefined;
        }

        // No UID: match by IP if present (any IP overlap)
        if (clientData.ip_addresses && clientData.ip_addresses.length > 0) {
            const byIp = clientList.find(c => c.ips && clientData.ip_addresses.some(ip => c.ips!.includes(ip)));
            if (byIp) return byIp;
        }

        // Finally try by hostname
        if (clientData.host_name) {
            const byName = clientList.find(c => c.name === clientData.host_name);
            if (byName) return byName;
        }

        return undefined;
    }, []);

    /**
     * Generate a unique ID for the client based on available data
     */
    const generateClientId = useCallback((clientData: ClientObj) => {
        // Prefer UID if available
        if (clientData.uid) return clientData.uid;
        // Otherwise use IP
        if (clientData.ip_addresses && clientData.ip_addresses.length > 0) return `ip:${clientData.ip_addresses[0]}`;
        // Lastly use hostname
        return `host:${clientData.host_name}`;
    }, []);

    /**
     * Create a new Client object from backend data
     */
    const createClient = useCallback((clientData: ClientObj, connected: boolean): Client => {
        return {
            id: generateClientId(clientData),
            uid: clientData.uid || undefined,
            name: clientData.host_name,
            ips: clientData.ip_addresses,
            status: connected ? 'online' : 'offline' as const,
            connectedAt: connected
                ? new Date(clientData.last_connection_date || clientData.first_connection_date)
                : undefined,
            monitors: clientData.monitors,
            placements: clientData.placements,
        };
    }, [generateClientId]);

    /**
     * Update client status (connected/disconnected)
     */
    const updateClientStatus = useCallback((clientData: ClientObj, connected: boolean) => {
        console.log(`[ClientManagement] ${connected ? 'Connecting' : 'Disconnecting'} client:`, clientData);

        setClients(prev => {
            const existing = findExistingClient(clientData, prev);

            if (existing) {
                console.log(`[ClientManagement] Found existing client:`, existing.id);

                // Update existing client
                const updated = prev.map(c => {
                    if (c.id === existing.id) {
                        return {
                            ...c,
                            uid: clientData.uid || c.uid, // Update UID if now available
                            name: clientData.host_name || c.name,
                            ips: clientData.ip_addresses || c.ips,
                            status: connected ? 'online' as const : 'offline' as const,
                            connectedAt: connected
                                ? new Date(clientData.last_connection_date || clientData.first_connection_date)
                                : c.connectedAt,
                            // Preserve cached monitors when the status payload doesn't carry them (transient).
                            monitors: clientData.monitors ?? c.monitors,
                            placements: clientData.placements ?? c.placements,
                        };
                    }
                    return c;
                });

                return updated;
            } else {
                console.log(`[ClientManagement] Adding new client`);

                // Add new client
                const newClient = createClient(clientData, connected);

                return [...prev, newClient];
            }
        });
    }, [findExistingClient, createClient]);

    /** Add a client manually (for authorization); lands unplaced until the Layout Editor positions it. */
    const addClient = useCallback((hostname: string, ips: string[]) => {
        const newClient: Client = {
            id: ips[0] || hostname,
            name: hostname,
            ips: ips,
            status: 'offline' as const,
        };

        setClients(prev => {
            // Check if it doesn't already exist
            const exists = prev.some(c =>
                (ips.length > 0 && c.ips && ips.some(ip => c.ips!.includes(ip))) ||
                (hostname && c.name === hostname)
            );

            if (exists) {
                console.warn(`[ClientManagement] Client already exists:`, hostname || ips[0]);
                return prev;
            }

            return [...prev, newClient];
        });
    }, []);

    /**
     * Remove a client
     */
    const removeClient = useCallback((clientId: string) => {
        setClients(prev => {
            return prev.filter(c => c.id !== clientId);
        });
    }, []);

    /**
     * Disconnect all clients (when stopping the server)
     */
    const disconnectAll = useCallback(() => {
        setClients(prev => prev.map(c => ({...c, status: 'offline' as const})));
    }, []);

    /**
     * Complete state reset
     */
    const reset = useCallback(() => {
        setClients([]);
    }, []);

    return {
        clients,
        connectedCount,
        updateClientStatus,
        addClient,
        removeClient,
        disconnectAll,
        reset,
    };
}
