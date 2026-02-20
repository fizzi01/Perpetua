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

import {StreamType} from "./Interface";

/**
 * Translate an enum (type) value to a snake_case string.
 * @param enumType - Enum type
 * @param value - Enum value
 * @returns String in snake_case format
 * @example
 * getType(EventType, EventType.ServiceInitialized) // "service_initialized"
 * getType(EventType, EventType.SslHandshakeStarted) // "ssl_handshake_started"
 */
export function getType<T extends Record<string, string | number>>(
    enumType: T,
    value: T[keyof T]
): string {
    const name = enumType[value as keyof T] as string;
    return name
        .replace(/([A-Z])/g, '_$1')
        .toLowerCase()
        .replace(/^_/, '');
}

/**
 * Convert an snake_case string to its corresponding enum value.
 * @param enumType The enum type
 * @param name The enum value
 * @returns The enum value or undefined if not found
 * @example
 * getEnumValue(EventType, "service_initialized") // EventType.ServiceInitialized
 * getEnumValue(EventType, "ssl_handshake_started") // EventType.SslHandshakeStarted
 */
export function getEnumValue<T extends Record<string, string | number>>(
    enumType: T,
    name: string
): T[keyof T] | undefined {
    // Convert snake_case to PascalCase
    const pascalCase = name
        .split('_')
        .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
        .join('');

    // Search for the value in the enum
    for (const [key, value] of Object.entries(enumType)) {
        if (key === pascalCase) {
            return value as T[keyof T];
        }
    }

    return undefined;
}

/**
 * Convert streams number in their specific enum representation.
 */
export function parseStreams(streams: Object): StreamType[] {
    const result: StreamType[] = [];
    // Streams object is like { "1": true, "4": false, "12": true }
    for (const [key, value] of Object.entries(streams)) {
        if (value) {
            const streamNum = parseInt(key, 10);
            if (streamNum in StreamType) {
                result.push(streamNum as StreamType);
            }
        }
    }
    return result;
}

/**
 * Check if the given string is a valid IP address.
 */
export function isValidIpAddress(ip: string): boolean {
    const ipv4Regex = /^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/;

    const ipv6Regex = /^(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$|^::(?:[0-9a-fA-F]{1,4}:){0,6}[0-9a-fA-F]{1,4}$|^[0-9a-fA-F]{1,4}::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}$|^(?:[0-9a-fA-F]{1,4}:){1,6}:$/;

    return ipv4Regex.test(ip) || ipv6Regex.test(ip);
}