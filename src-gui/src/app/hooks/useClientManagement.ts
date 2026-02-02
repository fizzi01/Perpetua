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

import { useState, useCallback } from 'react';
import { ClientObj } from '../api/Interface';

interface Client {
  id: string;
  uid?: string;
  name: string;
  ip: string;
  status: 'online' | 'offline';
  position: 'top' | 'bottom' | 'left' | 'right';
  connectedAt?: Date;
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
   * 1. If both have UID, match by UID
   * 2. Otherwise match by IP if present
   * 3. Finally match by hostname
   */
  const findExistingClient = useCallback((clientData: ClientObj, clientList: Client[]) => {
    // First try to find by UID if present
    if (clientData.uid) {
      const byUid = clientList.find(c => c.uid === clientData.uid);
      if (byUid) return byUid;
    }

    // Then try by IP if present
    if (clientData.ip_address) {
      const byIp = clientList.find(c => c.ip === clientData.ip_address);
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
    if (clientData.ip_address) return `ip:${clientData.ip_address}`;
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
      ip: clientData.ip_address,
      status: connected ? 'online' : 'offline' as const,
      position: clientData.screen_position as 'top' | 'bottom' | 'left' | 'right',
      connectedAt: connected 
        ? new Date(clientData.last_connection_date || clientData.first_connection_date)
        : undefined,
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
              ip: clientData.ip_address || c.ip,
              status: connected ? 'online' as const : 'offline' as const,
              connectedAt: connected 
                ? new Date(clientData.last_connection_date || clientData.first_connection_date)
                : c.connectedAt,
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

  /**
   * Add a client manually (for authorization)
   */
  const addClient = useCallback((hostname: string, ip: string, position: 'top' | 'bottom' | 'left' | 'right') => {
    const newClient: Client = {
      id: ip || hostname,
      name: hostname,
      ip: ip,
      status: 'offline' as const,
      position,
    };

    setClients(prev => {
      // Check if it doesn't already exist
      const exists = prev.some(c => 
        (ip && c.ip === ip) || 
        (hostname && c.name === hostname)
      );

      if (exists) {
        console.warn(`[ClientManagement] Client already exists:`, hostname || ip);
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
    setClients(prev => prev.map(c => ({ ...c, status: 'offline' as const })));
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
