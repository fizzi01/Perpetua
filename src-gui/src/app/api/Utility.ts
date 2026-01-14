
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