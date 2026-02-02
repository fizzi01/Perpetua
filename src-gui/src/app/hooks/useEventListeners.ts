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

import { useRef, useCallback, useEffect } from 'react';

type UnlistenFn = () => void;

interface ListenerEntry {
  unlisten: UnlistenFn;
  refCount: number;
}

/**
 * A custom hook to manage event listeners with automatic cleanup and reference counting.
 * @returns An object with methods to add, remove, and check listeners.
 */
export function useEventListeners(id: string = 'global') {
  const listenersRef = useRef<Map<string, ListenerEntry>>(new Map());

  const addListeners = useCallback((key: string, count: number, unlistenFn: UnlistenFn) => {
    for (let i = 0; i < count; i++) {
      addListener(key, unlistenFn);
    }
  }, []);

  const addListenerOnce = useCallback((key: string, unlistenFn: UnlistenFn) => {
    if (!listenersRef.current.has(key)) {
      addListener(key, unlistenFn);
      // console.log(`Listener "${key}" added once with ref count 1`);
    } else {
      // console.log(`Listener "${key}" already exists`);
      // unlisten current one
      unlistenFn();
    }
  }, []);

  const addListener = useCallback((key: string, unlistenFn: UnlistenFn) => {
    const existing = listenersRef.current.get(key);
    
    if (existing) {
      existing.refCount++;
      // console.log(`Listener "${key}" ref count increased to ${existing.refCount}`);
    } else {
      listenersRef.current.set(key, {
        unlisten: unlistenFn,
        refCount: 1
      });
      // console.log(`Listener "${key}" added with ref count 1`);
    }
  }, []);

  const removeListener = useCallback((key: string) => {
    const entry = listenersRef.current.get(key);
    
    if (!entry) {
      console.warn(`Listener "${key}" not found`);
      return;
    }

    entry.refCount--;
    // console.log(`Listener "${key}" ref count decreased to ${entry.refCount}`);

    // Remove the listener only when the ref count reaches 0
    if (entry.refCount <= 0) {
      entry.unlisten();
      listenersRef.current.delete(key);
      // console.log(`Listener "${key}" removed`);
    }
  }, []);

  const forceRemoveListener = useCallback((key: string) => {
    const entry = listenersRef.current.get(key);
    
    if (entry) {
      entry.unlisten();
      listenersRef.current.delete(key);
      // console.log(`Listener "${key}" forcefully removed`);
    } else {
      console.warn(`Listener "${key}" not found for forceful removal`);
    }
  }, []);

  const removeAll = useCallback(() => {
    listenersRef.current.forEach(entry => entry.unlisten());
    listenersRef.current.clear();
    console.log(`All listeners removed for "${id}"`);
  }, []);

  const hasListener = useCallback((key: string) => {
    return listenersRef.current.has(key);
  }, []);

  const getRefCount = useCallback((key: string): number => {
    return listenersRef.current.get(key)?.refCount ?? 0;
  }, []);

  useEffect(() => {
    return () => {
      console.log(`Cleaning up all listeners for "${id}"`);
      removeAll();
    };
  }, [removeAll]);

  return {
    addListeners,
    addListener,
    addListenerOnce,
    removeListener,
    forceRemoveListener,
    removeAll,
    hasListener,
    getRefCount,
  };
}