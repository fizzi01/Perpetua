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

// Payload exchanged with the main window. The main window emits
// "layout-editor:init" right after the editor window is shown; the
// editor window replies with "layout-editor:ready" so the main window
// knows it's safe to push state, and emits "layout-editor:save" on save
// (followed by closing itself).
export interface LayoutEditorInitPayload {
    serverMonitors: MonitorInfo[];
    clients: LayoutEditorClient[];
    placements: MonitorPlacement[];
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
    const [valid, setValid] = useState(true);
    const initialPlacementsRef = useRef<MonitorPlacement[]>([]);

    useEffect(() => {
        let unlisten: UnlistenFn | null = null;
        (async () => {
            unlisten = await listen<LayoutEditorInitPayload>(
                LAYOUT_INIT_EVENT,
                (event) => {
                    const data = event.payload;
                    setServerMonitors(data.serverMonitors || []);
                    setClients(data.clients || []);
                    setPlacements(data.placements || []);
                    initialPlacementsRef.current = data.placements || [];
                    setInitialised(true);
                },
            );
            // Tell the main window we're ready to receive state. Doing
            // this AFTER the listener is attached avoids a race where
            // the init event fires before we're listening.
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
            // Hiding might fail in dev with hot-reload; the main
            // window has the data anyway.
        }
    }

    async function handleCancel() {
        await emit(LAYOUT_CANCEL_EVENT, {});
        try {
            await getCurrentWindow().hide();
        } catch (_) {
            // ignored
        }
    }

    return (
        <div
            style={{
                width: "100vw",
                height: "100vh",
                padding: 10,
                boxSizing: "border-box",
                display: "flex",
                flexDirection: "column",
                gap: 8,
                backgroundColor: "var(--app-bg-secondary)",
                color: "var(--app-text-primary)",
                fontFamily: "system-ui, sans-serif",
            }}
        >
            <div style={{display: "flex", alignItems: "center", gap: 8}}>
                <span
                    style={{
                        fontSize: 13,
                        fontWeight: 600,
                        color: "var(--app-text-primary)",
                    }}
                >
                    Layout
                </span>
                <div style={{flex: 1}}/>
                <button
                    onClick={handleCancel}
                    style={{
                        padding: "5px 10px",
                        borderRadius: 5,
                        border: "1px solid var(--app-card-border)",
                        backgroundColor: "transparent",
                        color: "var(--app-text-primary)",
                        cursor: "pointer",
                        fontSize: 12,
                    }}
                >
                    Cancel
                </button>
                <button
                    onClick={handleSave}
                    disabled={!valid}
                    style={{
                        padding: "5px 12px",
                        borderRadius: 5,
                        border: "none",
                        backgroundColor: valid
                            ? "var(--app-primary)"
                            : "var(--app-bg-tertiary)",
                        color: valid ? "white" : "var(--app-text-muted)",
                        cursor: valid ? "pointer" : "not-allowed",
                        fontSize: 12,
                        fontWeight: 600,
                    }}
                >
                    Save
                </button>
            </div>

            <div
                style={{
                    flex: 1,
                    minHeight: 0,
                    border: "1px solid var(--app-card-border)",
                    borderRadius: 8,
                    padding: 8,
                    overflow: "hidden",
                    backgroundColor: "var(--app-card-bg)",
                    display: "flex",
                }}
            >
                {!initialised ? (
                    <div
                        style={{
                            fontSize: 12,
                            color: "var(--app-text-muted)",
                            margin: "auto",
                        }}
                    >
                        Waiting for layout…
                    </div>
                ) : (
                    <LayoutEditor
                        serverMonitors={serverMonitors}
                        clients={clients}
                        placements={placements}
                        onChange={setPlacements}
                        onValidityChange={(ok) => setValid(ok)}
                        height={Math.max(360, window.innerHeight - 80)}
                    />
                )}
            </div>
        </div>
    );
}

// Exported so the main window can use the same event names without
// re-declaring strings.
export const LAYOUT_EDITOR_EVENTS = {
    INIT: LAYOUT_INIT_EVENT,
    READY: LAYOUT_READY_EVENT,
    SAVE: LAYOUT_SAVE_EVENT,
    CANCEL: LAYOUT_CANCEL_EVENT,
};
