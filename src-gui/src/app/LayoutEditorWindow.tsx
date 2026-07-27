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

import {useCallback, useEffect, useRef, useState} from "react";
import {emit, listen, type UnlistenFn} from "@tauri-apps/api/event";
import {getCurrentWindow} from "@tauri-apps/api/window";
import {motion} from "motion/react";

import {LayoutEditor, type LayoutEditorClient} from "./components/LayoutEditor";
import type {MonitorInfo, MonitorPlacement} from "./api/Interface";


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
    const closingRef = useRef(false);

    
    const closeEditor = useCallback(async (emitCancel: boolean) => {
        if (closingRef.current) return;
        closingRef.current = true;
        if (emitCancel) {
            try {
                await emit(LAYOUT_CANCEL_EVENT, {});
            } catch (err) {
                console.error("Failed to emit layout editor cancel", err);
            }
        }
        try {
            await getCurrentWindow().hide();
        } catch (_) {
            // hot-reload in dev can race here; state cleanup still matters.
        }
        initialisedRef.current = false;
        setInitialised(false);
    }, []);

    useEffect(() => {
        const handleResize = () => setWindowHeight(window.innerHeight);
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

    useEffect(() => {
        let unlisten: UnlistenFn | null = null;
        (async () => {
            unlisten = await getCurrentWindow().onCloseRequested(async (event) => {
                event.preventDefault();
                await closeEditor(true);
            });
        })();
        return () => {
            if (unlisten) unlisten();
        };
    }, [closeEditor]);

    useEffect(() => {
        let unlisten: UnlistenFn | null = null;
        (async () => {
            unlisten = await listen<LayoutEditorInitPayload>(
                LAYOUT_INIT_EVENT,
                (event) => {
                    closingRef.current = false;
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
        await closeEditor(false);
    }

    async function handleCancel() {
        await closeEditor(true);
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
                <motion.button
                    type="button"
                    whileHover={{scale: 1.02}}
                    whileTap={{scale: 0.98}}
                    onClick={handleCancel}
                    className="px-4 py-2 rounded-lg border text-sm font-semibold transition-all duration-200 shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                    style={{
                        borderColor: "var(--app-input-border)",
                        backgroundColor: "var(--app-bg-tertiary)",
                        color: "var(--app-text-primary)",
                        cursor: "pointer",
                        outlineColor: "var(--app-primary-light)",
                    }}
                >
                    Cancel
                </motion.button>
                <motion.button
                    type="button"
                    whileHover={valid ? {scale: 1.02} : undefined}
                    whileTap={valid ? {scale: 0.98} : undefined}
                    onClick={handleSave}
                    disabled={!valid}
                    className="px-4 py-2 rounded-lg border-none text-sm font-semibold transition-all duration-200 shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                    style={{
                        backgroundColor: valid
                            ? "var(--app-primary)"
                            : "var(--app-bg-tertiary)",
                        color: valid ? "white" : "var(--app-text-muted)",
                        cursor: valid ? "pointer" : "not-allowed",
                        outlineColor: "var(--app-primary-light)",
                    }}
                >
                    Save Layout
                </motion.button>
            </div>

            <div
                className="flex-1 flex overflow-hidden rounded-xl shadow-sm"
                style={{
                    minHeight: 0,
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
