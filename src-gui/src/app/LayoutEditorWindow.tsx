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
 */

import {useEffect, useRef, useState} from "react";
import {emit, listen, type UnlistenFn} from "@tauri-apps/api/event";
import {getCurrentWindow} from "@tauri-apps/api/window";

import {LayoutEditor, type LayoutEditorClient} from "./components/LayoutEditor";
import type {MonitorInfo, MonitorPlacement} from "./api/Interface";
import {platform} from '@tauri-apps/plugin-os';

// Payload exchanged with the main window. Main emits "init", editor replies "ready", then "save"/"cancel".
export interface LayoutEditorInitPayload {
    serverMonitors: MonitorInfo[];
    clients: LayoutEditorClient[];
    placements: MonitorPlacement[];
    preselectClientUid?: string;
}

export interface LayoutEditorSavePayload {
    placements: MonitorPlacement[];
}

const LAYOUT_INIT_EVENT = "layout-editor:init";
const LAYOUT_READY_EVENT = "layout-editor:ready";
const LAYOUT_SAVE_EVENT = "layout-editor:save";
const LAYOUT_CANCEL_EVENT = "layout-editor:cancel";

export default function LayoutEditorWindow() {
    const [initialised, setInitialised] = useState(false);
    const [serverMonitors, setServerMonitors] = useState<MonitorInfo[]>([]);
    const [clients, setClients] = useState<LayoutEditorClient[]>([]);
    const [placements, setPlacements] = useState<MonitorPlacement[]>([]);
    const [preselectClientUid, setPreselectClientUid] = useState<
        string | undefined
    >(undefined);
    const [valid, setValid] = useState(true);
    const [windowHeight, setWindowHeight] = useState(window.innerHeight);
    const initialPlacementsRef = useRef<MonitorPlacement[]>([]);
    // Mirror of `initialised` for the persistent listener: re-INIT must refresh sidebar without clobbering drags.
    const initialisedRef = useRef(false);

    const currentPlatform = platform();
    const root = document.documentElement;
    root.style.setProperty('--border-radius', currentPlatform === 'windows' ? '0px' : '14px');

    useEffect(() => {
        const handleResize = () => setWindowHeight(window.innerHeight);
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

    useEffect(() => {
        let unlisten: UnlistenFn | null = null;
        (async () => {
            unlisten = await listen<LayoutEditorInitPayload>(
                LAYOUT_INIT_EVENT,
                (event) => {
                    const data = event.payload;
                    setServerMonitors(data.serverMonitors || []);
                    setClients(data.clients || []);
                    setPreselectClientUid(data.preselectClientUid);
                    // Seed placements only on the first INIT; later INITs are live refreshes — preserve drags.
                    if (!initialisedRef.current) {
                        setPlacements(data.placements || []);
                        initialPlacementsRef.current = data.placements || [];
                        initialisedRef.current = true;
                        setInitialised(true);
                    }
                },
            );
            // Emit AFTER attaching the listener — otherwise INIT can fire before we're listening.
            await emit(LAYOUT_READY_EVENT, {});
        })();
        return () => {
            if (unlisten) unlisten();
        };
    }, []);

    async function handleSave() {
        if (!valid) return;
        const payload: LayoutEditorSavePayload = {placements};
        await emit(LAYOUT_SAVE_EVENT, payload);
        try {
            await getCurrentWindow().hide();
        } catch (_) {
            // hot-reload in dev can race here; data already left the window.
        }
        initialisedRef.current = false;
        setInitialised(false);
    }

    async function handleCancel() {
        await emit(LAYOUT_CANCEL_EVENT, {});
        try {
            await getCurrentWindow().hide();
        } catch (_) {
            // ignored
        }
        initialisedRef.current = false;
        setInitialised(false);
    }

    return (
        <div
            data-tauri-drag-region
            className="w-screen h-screen p-4 box-border flex flex-col gap-3"
            style={{
                backgroundColor: "var(--app-bg-secondary)",
                color: "var(--app-text-primary)",
            }}
        >
            <div data-tauri-drag-region className="flex items-center gap-3 select-none">
                <span
                    className="text-lg font-bold"
                    style={{ color: "var(--app-text-primary)" }}
                >
                    Layout Configuration
                </span>
                <div data-tauri-drag-region style={{ flex: 1, height: "100%" }} />
                <button
                    onClick={handleCancel}
                    className="px-4 py-2 rounded-lg border text-sm font-medium transition-colors"
                    style={{
                        borderColor: "var(--app-card-border)",
                        backgroundColor: "transparent",
                        color: "var(--app-text-primary)",
                        cursor: "pointer",
                    }}
                >
                    Cancel
                </button>
                <button
                    onClick={handleSave}
                    disabled={!valid}
                    className="px-4 py-2 rounded-lg border-none text-sm font-semibold transition-colors shadow-sm"
                    style={{
                        backgroundColor: valid
                            ? "var(--app-primary)"
                            : "var(--app-bg-tertiary)",
                        color: valid ? "white" : "var(--app-text-muted)",
                        cursor: valid ? "pointer" : "not-allowed",
                    }}
                >
                    Save Layout
                </button>
            </div>

            <div
                className="flex-1 flex overflow-hidden rounded-xl border shadow-sm"
                style={{
                    minHeight: 0,
                    borderColor: "var(--app-card-border)",
                    backgroundColor: "var(--app-card-bg)",
                }}
            >
                {!initialised ? (
                    <div
                        className="m-auto text-sm font-medium"
                        style={{ color: "var(--app-text-muted)" }}
                    >
                        Waiting for layout…
                    </div>
                ) : (
                    <LayoutEditor
                        serverMonitors={serverMonitors}
                        clients={clients}
                        placements={placements}
                        preselectClientUid={preselectClientUid}
                        onChange={setPlacements}
                        onValidityChange={(ok) => setValid(ok)}
                        height={Math.max(360, windowHeight - 90)}
                    />
                )}
            </div>
        </div>
    );
}

export const LAYOUT_EDITOR_EVENTS = {
    INIT: LAYOUT_INIT_EVENT,
    READY: LAYOUT_READY_EVENT,
    SAVE: LAYOUT_SAVE_EVENT,
    CANCEL: LAYOUT_CANCEL_EVENT,
};
