// src/app/hooks/useEventListeners.ts
import { useRef, useCallback, useEffect } from 'react';

type UnlistenFn = () => void;

/**
 * A custom hook to manage event listeners with automatic cleanup.
 * @returns An object with methods to add, remove, and check listeners.
 */
export function useEventListeners() {
  const listenersRef = useRef<Map<string, UnlistenFn>>(new Map());

  const addListener = useCallback((key: string, unlistenFn: UnlistenFn) => {
    if (listenersRef.current.has(key)) {
      listenersRef.current.get(key)?.();
    }
    listenersRef.current.set(key, unlistenFn);
  }, []);

  const removeListener = useCallback((key: string) => {
    const unlisten = listenersRef.current.get(key);
    if (unlisten) {
      unlisten();
      listenersRef.current.delete(key);
    }
  }, []);

  const removeAll = useCallback(() => {
    listenersRef.current.forEach(unlisten => unlisten());
    listenersRef.current.clear();
  }, []);

  const hasListener = useCallback((key: string) => {
    return listenersRef.current.has(key);
  }, []);

  // Cleanup automatico quando il componente si smonta
  useEffect(() => {
    return () => {
      removeAll();
    };
  }, [removeAll]);

  return {
    addListener,
    removeListener,
    removeAll,
    hasListener,
  };
}