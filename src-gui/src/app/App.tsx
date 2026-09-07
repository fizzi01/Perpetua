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

import {useEffect, useState} from 'react';
import {BrowserRouter, Route, Routes} from 'react-router-dom';
import {ClientTab} from './components/client-tab';
import {ServerTab} from './components/server-tab';
import {Titlebar} from './components/titlebar';
import {motion} from 'motion/react';
import {ServiceTabSkeleton} from './components/ui/service-tab-skeleton';

import {
    CommandType,
    EventType,
    PermissionInfo,
    PermissionsRequiredData,
    PermissionsResult,
} from './api/Interface';

import {getPermissions} from './api/Sender';
import {listenCommand, listenGeneralEvent} from './api/Listener';
import {PermissionGate} from './components/ui/permission-gate';
import {useDaemonSync} from './hooks/useDaemonSync';
import {useAppSelector} from './hooks/redux';
import {ScrollArea} from './components/ui/scrollbar';
import LogsWindow from './LogsWindow';
import {openLogWindow} from './api/logWindow';
import {SplashScreen} from './Splash';
import LayoutEditorWindow from './LayoutEditorWindow';

export function Main() {

    const sync = useDaemonSync();
    const {mode} = sync;
    const [disableModeSwitch, setDisableModeSwitch] = useState<boolean>(false);
    const [logWindowError, setLogWindowError] = useState('');
    const showLogs = () => {
        setLogWindowError('');
        void openLogWindow().catch(err => setLogWindowError(String(err)));
    };
    // OS-level permission gate (macOS Accessibility / Input Monitoring). Null
    // when nothing is missing; a non-empty list drives the blocking overlay.
    const [missingPerms, setMissingPerms] = useState<PermissionInfo[] | null>(null);
    const [pendingService, setPendingService] = useState<string | null>(null);
    // True when the gate is up because a permission was revoked at runtime.
    const [permsRevoked, setPermsRevoked] = useState<boolean>(false);
    const serverState = useAppSelector(state => state.server);
    const clientState = useAppSelector(state => state.client);

    // Register before querying, and release even registrations that finish
    // after cleanup (including StrictMode's first mount).
    useEffect(() => {
        let disposed = false;
        let permissionsMissing = false;
        let registered = false;
        const unlisteners: (() => void)[] = [];
        const register = async (promise: Promise<() => void>) => {
            const unlisten = await promise;
            if (disposed) unlisten();
            else unlisteners.push(unlisten);
        };
        const queryPermissions = () => getPermissions().catch(err => {
            if (!disposed) console.error('[App] Error fetching permissions:', err);
        });
        Promise.all([
            register(listenGeneralEvent(EventType.PermissionsRequired, false, event => {
                if (disposed) return;
                const data = event.data as PermissionsRequiredData | undefined;
                const perms = data?.permissions ?? [];
                permissionsMissing = perms.length > 0;
                setPendingService(data?.pending_service ?? null);
                setPermsRevoked(data?.revoked === true);
                setMissingPerms(permissionsMissing ? perms : null);
            })),
            register(listenGeneralEvent(EventType.PermissionsGranted, true, () => {
                if (disposed) return;
                permissionsMissing = false;
                setMissingPerms(null);
                setPendingService(null);
                setPermsRevoked(false);
            })),
            register(listenCommand(EventType.CommandSuccess, CommandType.GetPermissions, event => {
                if (disposed) return;
                const result = event.data?.result as PermissionsResult | undefined;
                const missing = result?.missing ?? [];
                permissionsMissing = missing.length > 0;
                setPendingService(result?.pending_service ?? null);
                setMissingPerms(permissionsMissing ? missing : null);
            })),
            register(listenGeneralEvent(EventType.ShowLog, true, () => {
                if (!disposed) showLogs();
            })),
            register(listenGeneralEvent(EventType.MonitorTopologyChanged, true, () => {
                if (!disposed) sync.refresh();
            })),
        ]).then(() => {
            if (disposed) return;
            registered = true;
            return queryPermissions();
        }).catch(err => {
            if (!disposed) console.error('[App] Error registering event listeners:', err);
        });
        const poll = setInterval(() => {
            if (registered && permissionsMissing) void queryPermissions();
        }, 2500);
        return () => {
            disposed = true;
            clearInterval(poll);
            unlisteners.forEach(unlisten => unlisten());
        };
    }, []);

    return (
        <div className="w-full h-full flex items-start justify-start overflow-hidden"
             style={{backgroundColor: 'var(--app-bg)'}}>
            <div className="w-full h-full flex flex-col overflow-hidden min-h-0"
                 style={{backgroundColor: 'var(--app-bg-secondary)', borderColor: 'var(--app-border)'}}>
                {/* Titlebar */}
                <Titlebar disabled={disableModeSwitch || sync.status !== 'ready'} mode={mode}
                          onModeChange={sync.changeMode}/>
                {/* Scrollable Content */}
                <ScrollArea extraPadding='pl-10' className={`flex-1 min-h-0 overflow-y-auto px-8 py-6 relative`}>
                    {sync.error ? (
                        <div className="p-4 mb-4 rounded-lg border text-sm space-y-3"
                             style={{backgroundColor: 'var(--app-card-bg)', borderColor: 'var(--app-border)', color: 'var(--app-text-primary)'}}>
                            <p role="alert">
                                {sync.error}
                            </p>
                            <div className="flex items-center gap-3">
                                {sync.status === 'error' && (
                                    <button type="button" onClick={sync.retry} className="px-3 py-1.5 rounded-md border cursor-pointer"
                                            style={{borderColor: 'var(--app-border)'}}>Retry</button>
                                )}
                                <button type="button" onClick={() => showLogs()} className="text-xs underline cursor-pointer">
                                    Open logs
                                </button>
                            </div>
                        </div>
                    ) : null}
                    {sync.status === 'loading' && !sync.hasState && (
                        <>
                            <ServiceTabSkeleton mode={mode}/>
                            <button type="button" onClick={() => showLogs()}
                                    className="mt-4 text-xs cursor-pointer hover:underline"
                                    style={{color: 'var(--app-text-muted)'}}>Open logs</button>
                        </>
                    )}
                    {/* Content */}
                    {sync.hasState && <div
                        // Retain the last valid tab during a switch, but prevent
                        // commands from racing the pending service choice.
                        {...{inert: sync.status !== 'ready' ? '' : undefined}}
                        aria-busy={sync.status !== 'ready'}
                        key={mode}
                    >
                        <motion.div
                            initial={{opacity: 0, scale: 0.95}}
                            animate={{opacity: 1, scale: 1}}
                            transition={{duration: 0.3}}
                        >
                            {mode === 'client' ? <ClientTab onStatusChange={setDisableModeSwitch} state={clientState}/> :
                                <ServerTab onStatusChange={setDisableModeSwitch} state={serverState}/>}
                        </motion.div>
                    </div>}
                    {logWindowError && <p role="alert" className="mt-3 text-xs">{logWindowError}</p>}
                </ScrollArea>
            </div>
            {missingPerms && missingPerms.length > 0 ? (
                <PermissionGate missing={missingPerms} pendingService={pendingService} revoked={permsRevoked}/>
            ) : null}
        </div>
    );
}

export default function App() {
    return (
        <BrowserRouter>
            <Routes>
                <Route path="/" element={<Main/>}/>
                <Route path="/splashscreen" element={<SplashScreen/>}/>
                <Route path="/logs" element={<LogsWindow/>}/>
                <Route path="/layout-editor" element={<LayoutEditorWindow/>}/>
            </Routes>
        </BrowserRouter>
    )
}
