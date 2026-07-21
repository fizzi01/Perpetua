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

import {useEffect, useRef, useState} from 'react';
import {BrowserRouter, Route, Routes} from 'react-router-dom';
import {ClientTab} from './components/client-tab';
import {ServerTab} from './components/server-tab';
import {Titlebar} from './components/titlebar';
import {motion} from 'motion/react';

import {
    ClientStatus,
    CommandType,
    EventType,
    PermissionInfo,
    PermissionsRequiredData,
    PermissionsResult,
    ServerStatus,
    ServiceStatus,
} from './api/Interface';

import {chooseService, getPermissions, getStatus} from './api/Sender';
import {listenCommand, listenGeneralEvent} from './api/Listener';
import {PermissionGate} from './components/ui/permission-gate';
import {useEventListeners} from './hooks/useEventListeners';
import {useAppDispatch, useAppSelector} from './hooks/redux';
import {ActionType} from './store/actions';
import {ScrollArea} from './components/ui/scrollbar';
import {DaemonLogDialog} from './components/ui/DaemonLogDialog';
import {SplashScreen} from './Splash';
import LayoutEditorWindow from './LayoutEditorWindow';

export function Main() {

    const [mode, setMode] = useState<'client' | 'server'>('client');
    const [disableModeSwitch, setDisableModeSwitch] = useState<boolean>(false);
    const [stateListenersAdded, setListenersAdded] = useState<boolean>(false);
    const [showLogs, setShowLogs] = useState<boolean>(false);
    // OS-level permission gate (macOS Accessibility / Input Monitoring). Null
    // when nothing is missing; a non-empty list drives the blocking overlay.
    const [missingPerms, setMissingPerms] = useState<PermissionInfo[] | null>(null);
    const [pendingService, setPendingService] = useState<string | null>(null);

    const listeners = useEventListeners();

    const serverState = useAppSelector(state => state.server);
    const clientState = useAppSelector(state => state.client);
    const dispatch = useAppDispatch();

    const isStartupRef = useRef(true);

    function firstStartup() {
        let isStartup = isStartupRef.current;
        if (isStartup) {
            console.log('[App] First startup detected, choosing service and setting up listeners');
            setupStatusListener();
            isStartupRef.current = false;
            chooseService(mode).catch((err) => {
                console.error('[App] Error changing service:', err);
                listeners.forceRemoveListener('service-choice');
            });

            getStatus().catch((err) => {
                console.error('[App] Error fetching status:', err);
            });

            // OS permission gate: the daemon pushes ``permissions_required`` when
            // it defers startup on a missing macOS permission, and
            // ``permissions_granted`` once it observes the grant.
            listenGeneralEvent(EventType.PermissionsRequired, false, (event: any) => {
                console.log('[App] PermissionsRequired event received', event);
                const data = event.data as PermissionsRequiredData | undefined;
                const perms = data?.permissions ?? [];
                setPendingService(data?.pending_service ?? null);
                setMissingPerms(perms.length > 0 ? perms : null);
            }).then((unlisten) => {
                listeners.addListenerOnce('permissions-required', unlisten);
            });

            listenGeneralEvent(EventType.PermissionsGranted, true, (event: any) => {
                console.log('[App] PermissionsGranted event received', event);
                setMissingPerms(null);
                setPendingService(null);
            }).then((unlisten) => {
                listeners.addListenerOnce('permissions-granted', unlisten);
            });

            // Ask for the current permission state on startup so the gate shows
            // immediately on a manual launch too (not only on autostart).
            setupPermissionCheckListener();
            getPermissions().catch((err) => {
                console.error('[App] Error fetching permissions:', err);
            });

            listenGeneralEvent(EventType.ShowLog, true, (event: any) => {
                console.log('[App] ShowLog event received', event);
                setShowLogs(true);
            }).then((unlisten) => {
                listeners.addListenerOnce('show-log', () => {
                    console.log('[App]Removing ShowLog listener');
                    unlisten();
                });
            });

            // Server-side monitor topology changed (display added/removed,
            // resolution change). Pull a fresh STATUS so the layout editor's
            // monitor list and any orphan warnings reach the GUI.
            listenGeneralEvent(EventType.MonitorTopologyChanged, true, (event: any) => {
                console.log('[App] MonitorTopologyChanged event received', event);
                setupStatusListener();
                getStatus().catch((err) => {
                    console.error('[App] Error fetching status after monitor change:', err);
                });
            }).then((unlisten) => {
                listeners.addListenerOnce('monitor-topology-changed', () => {
                    unlisten();
                });
            });
        }
    }

    function setupStatusListener() {
        setListenersAdded(true);
        listenCommand(EventType.CommandSuccess, CommandType.Status, (event) => {
            // console.log(`Status received`, event);
            let result = event.data?.result as ServiceStatus;
            let server_status = result.server_info as ServerStatus;
            let client_status = result.client_info as ClientStatus;
            if (server_status) {
                if (server_status.running) {
                    setMode('server');
                }
                // Dispatch action to update server state
                dispatch({type: ActionType.SERVER_STATE, payload: server_status});
            }
            if (client_status) {
                // Update client state in the store
                if (client_status.running) {
                    setMode('client');
                }
                // Dispatch action to update client state
                dispatch({type: ActionType.CLIENT_STATE, payload: client_status});
            }
            listeners.removeListener('status');
        }).then((unlisten) => {
            listeners.addListenerOnce('status', () => {
                // console.log('Removing status listener');
                unlisten();
                setListenersAdded(false);
            });
        });
    }

    // One-shot listener for the ``get_permissions`` query result. Re-registered
    // on every poll (mirrors setupStatusListener).
    function setupPermissionCheckListener() {
        listenCommand(EventType.CommandSuccess, CommandType.GetPermissions, (event) => {
            const result = event.data?.result as PermissionsResult | undefined;
            const missing = result?.missing ?? [];
            setPendingService(result?.pending_service ?? null);
            setMissingPerms(missing.length > 0 ? missing : null);
            listeners.removeListener('get-permissions');
        }).then((unlisten) => {
            listeners.addListenerOnce('get-permissions', unlisten);
        });
    }

    useEffect(() => {
        firstStartup();
    }, []);

    useEffect(() => {
        if (!stateListenersAdded) {
            console.log('Setting up event listeners');
            return () => {
                setupStatusListener();

                getStatus().catch((err) => {
                    console.error('Error fetching status:', err);
                });
            };
        }

    }, [mode]);

    // While the permission gate is up, re-query the daemon so the overlay
    // reflects a grant even if the ``permissions_granted`` push is missed.
    useEffect(() => {
        if (missingPerms === null) return;
        const interval = setInterval(() => {
            setupPermissionCheckListener();
            getPermissions().catch((err) => {
                console.error('[App] Error polling permissions:', err);
            });
        }, 2500);
        return () => clearInterval(interval);
    }, [missingPerms]);

    // Periodically fetch status to avoid desync
    useEffect(() => {
        const interval = setInterval(() => {
            console.log('[App] Fetching status');
            setupStatusListener();
            getStatus().catch((err) => {
                console.error('[App] Error fetching status:', err);
            });
        }, 2000); // 2 seconds

        return () => clearInterval(interval);
    }, []);

    function changeMode(newMode: 'client' | 'server', force: boolean = false) {
        console.log(`Changing mode to ${newMode} (force: ${force}, previous: ${mode})`);
        if (newMode === mode && !force) return;

        listenCommand(EventType.CommandSuccess, CommandType.ServiceChoice, (event) => {
            console.log(`Service choice changed successfully: ${event.message}`);
            let mode = event.message?.toLowerCase();
            if (mode === 'client' || mode === 'server') {
                setMode(mode);
            }
            listeners.removeListener('service-choice');
        }).then((unlisten) => {
            listeners.addListenerOnce('service-choice', unlisten);
        });

        chooseService(newMode).catch((err) => {
            console.error('Error changing service:', err);
            listeners.forceRemoveListener('service-choice');
        });
    }

    return (
        <div className="w-full h-full flex items-start justify-start overflow-hidden"
             style={{backgroundColor: 'var(--app-bg)'}}>
            <div className="w-full h-full flex flex-col overflow-hidden min-h-0"
                 style={{backgroundColor: 'var(--app-bg-secondary)', borderColor: 'var(--app-border)'}}>
                {/* Titlebar */}
                <Titlebar disabled={disableModeSwitch} mode={mode} onModeChange={(newMode) => {
                    changeMode(newMode);

                    // Fetch status after changing service
                    getStatus().catch((err) => {
                        console.error('Error fetching status:', err);
                        listeners.forceRemoveListener('service-choice');
                    });
                }}/>
                {/* Scrollable Content */}
                <ScrollArea extraPadding='pl-10' className={`flex-1 min-h-0 overflow-y-auto px-8 py-6 relative`}>
                    {/* Content */}
                    <motion.div
                        key={mode}
                        initial={{opacity: 0, scale: 0.95}}
                        animate={{opacity: 1, scale: 1}}
                        transition={{duration: 0.3}}
                    >
                        {mode === 'client' ? <ClientTab onStatusChange={setDisableModeSwitch} state={clientState}/> :
                            <ServerTab onStatusChange={setDisableModeSwitch} state={serverState}/>}
                    </motion.div>
                    <DaemonLogDialog isOpen={showLogs} onClose={() => setShowLogs(false)}/>
                </ScrollArea>
            </div>
            {missingPerms && missingPerms.length > 0 ? (
                <PermissionGate missing={missingPerms} pendingService={pendingService}/>
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
                <Route path="/layout-editor" element={<LayoutEditorWindow/>}/>
            </Routes>
        </BrowserRouter>
    )
}