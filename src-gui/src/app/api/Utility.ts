import { StreamType } from "./Interface";

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
  // Converte snake_case in PascalCase
  const pascalCase = name
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join('');
  
  // Cerca il valore nell'enum
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