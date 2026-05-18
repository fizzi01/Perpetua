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

import {Switch} from "./ui/switch";

import {ScrollArea} from './ui/scrollbar';

import {Activity, Info, Key, Layout as LayoutIcon, Lock, Plus, Settings, Shield, Trash2, Users, X} from 'lucide-react';
import {AnimatePresence, motion} from 'motion/react';
import {InlineNotification, Notification} from './ui/inline-notification';
import {PowerButton} from './ui/power-button';
import {PermissionsPanel} from './ui/permissions-panel';
import {PendingApprovalCard} from './ui/pending-approval-card';

import {useEventListeners} from '../hooks/useEventListeners';
import {useClientManagement} from '../hooks/useClientManagement';
import {
    addClient as addClientCommand,
    approveClient as approveClientCommand,
    denyClient as denyClientCommand,
    getLocalIpAddress,
    removeClient as removeClientCommand,
    saveServerConfig,
    setClientLayout,
    shareCertificate,
    startServer,
    stopServer,
    switchTrayIcon
} from '../api/Sender';
import {listenCommand, listenGeneralEvent} from '../api/Listener';
import {
    ClientApprovalRequest,
    ClientApprovalResolved,
    ClientEditObj,
    ClientObj,
    CommandType,
    EventType,
    OtpInfo,
    PairingRequestInfo,
    ServerStatus,
    StreamType
} from '../api/Interface';

import {ServerTabProps} from '../commons/Tab'
import {isValidIpAddress, parseStreams} from '../api/Utility'
import {abbreviateText, CopyableBadge} from './ui/copyable-badge';
import {ActionButton} from './ui/action-button';
import type {MonitorInfo, MonitorPlacement} from '../api/Interface';
import {
    LAYOUT_EDITOR_EVENTS,
    type LayoutEditorInitPayload,
    type LayoutEditorSavePayload,
} from '../LayoutEditorWindow';
import {emit, listen, type UnlistenFn} from '@tauri-apps/api/event';
import {Window} from '@tauri-apps/api/window';

export function ServerTab({onStatusChange, state}: ServerTabProps) {
    let previousState = useRef<ServerStatus | null>(null);

    const [runningPending, setRunningPending] = useState(false);
    const [isRunning, setIsRunning] = useState(state.running);
    const [showOptions, setShowOptions] = useState(false);
    const [showClients, setShowClients] = useState(false);
    const [showSecurity, setShowSecurity] = useState(false);
    // Local working-copy of the workspace placements (per client
    // monitor). Today this is GUI-only state; future work will persist
    // it via a SetLayout command on the daemon and load it back from
    // ServerStatus.
    const [layoutPlacements, setLayoutPlacements] = useState<MonitorPlacement[]>([]);
    // Server monitors are populated from the daemon's status payload
    // (see ``_handle_status`` server-side, which now serialises
    // ``Screen.get_monitors()``). Falls back to an empty list when the
    // OS backend can't enumerate displays; the editor then renders a
    // single virtual placeholder so the user can still drag things
    // around.
    const [serverMonitors, setServerMonitors] = useState<MonitorInfo[]>(
        state.monitors ?? [],
    );

    useEffect(() => {
        // Keep the local cache in sync with status updates. We compare
        // by JSON because MonitorInfo is a plain dict and the array
        // identity changes on every status refresh.
        const incoming = state.monitors ?? [];
        setServerMonitors((prev) => {
            if (JSON.stringify(prev) === JSON.stringify(incoming)) return prev;
            return incoming;
        });
    }, [state.monitors]);

    useEffect(() => {
        // Seed the working layout from the daemon's authoritative client
        // list whenever it changes. The daemon stores ``placements`` per
        // ClientObj; flatten them so the editor renders the persisted
        // state on startup instead of a blank canvas. We always overwrite
        // - the editor saves through SetClientLayout (which dispatches
        // CLIENT_LAYOUT_UPDATED), so authorized_clients is the source of
        // truth.
        const incoming: MonitorPlacement[] = [];
        for (const c of state.authorized_clients ?? []) {
            for (const p of c.placements ?? []) {
                incoming.push({
                    client_uid: c.uid,
                    client_monitor_id: p.client_monitor_id,
                    workspace_x: p.workspace_x,
                    workspace_y: p.workspace_y,
                    width: p.width,
                    height: p.height,
                });
            }
        }
        setLayoutPlacements((prev) => {
            if (JSON.stringify(prev) === JSON.stringify(incoming)) return prev;
            return incoming;
        });
    }, [state.authorized_clients]);
    const [uid, setUid] = useState(state.uid);
    const [port, setPort] = useState(state.port.toString());
    const [host, setHost] = useState(state.host);
    const [enableMouse, setEnableMouse] = useState(parseStreams(state.streams_enabled).includes(StreamType.Mouse));
    const [enableKeyboard, setEnableKeyboard] = useState(parseStreams(state.streams_enabled).includes(StreamType.Keyboard));
    const [enableClipboard, setEnableClipboard] = useState(parseStreams(state.streams_enabled).includes(StreamType.Clipboard));
    const [requireSSL, setRequireSSL] = useState(state.ssl_enabled);
    const [otp, setOtp] = useState('');
    const [otpRequested, setOtpRequested] = useState(false);
    const [otpTimeout, setOtpTimeout] = useState(30);
    const [otpExpiresAt, setOtpExpiresAt] = useState<number | null>(null);
    const [otpRemaining, setOtpRemaining] = useState(0);
    const [pairingRequester, setPairingRequester] = useState('');
    // Pending client-approval prompts: unknown clients trying to connect that
    // the admin must allow/deny. Keyed by peer_ip so duplicate handshakes
    // collapse onto the same prompt.
    const [pendingApprovals, setPendingApprovals] = useState<ClientApprovalRequest[]>([]);
    const [clientIpTags, setClientIpTags] = useState<string[]>([]);
    const [clientIpInput, setClientIpInput] = useState('');
    const [isAddingClient, setIsAddingClient] = useState(false);

    const [uptime, setUptime] = useState(() => {
        if (state.start_time) {
            let startDate = new Date(state.start_time);
            let now = new Date();
            return Math.floor((now.getTime() - startDate.getTime()) / 1000);
        }
        return 0;
    });
    const [notifications, setNotifications] = useState<Notification[]>([]);

    const clientManager = useClientManagement();
    const listeners = useEventListeners('server-tab');
    const clientEventHandler = handleClientEventListeners();

    // Always-fresh snapshot of the client list. The layout-editor SAVE
    // listener is registered inside ``openLayoutEditorWindow``, which
    // captures ``clientManager`` by closure. When the editor is auto-
    // opened right after Allow, the closure's snapshot does NOT yet
    // include the freshly approved client (its ``ClientConnected``
    // event lands a tick later). Reading the ref inside the SAVE
    // handler instead of the captured ``clientManager`` keeps the
    // grouping + UID lookup correct so ``setClientLayout`` is invoked
    // with the real ``client_uid`` rather than ``undefined`` (which
    // the daemon rejects with "Must provide client_uid, hostname, or
    // ip_address").
    const clientsRef = useRef(clientManager.clients);
    clientsRef.current = clientManager.clients;

    const otpFocus = useRef<HTMLDivElement>(null);
    const saveOptionsTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const ipInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (state.host === '' || state.host === '0.0.0.0') {
            getLocalIpAddress().then(ip => setHost(ip));
        }
    }, []);

    const addNotification = (type: Notification['type'], message: string, description?: string) => {
        const newNotification: Notification = {
            id: Date.now().toString(),
            type,
            message,
            description,
        };
        setNotifications((prev) => [...prev, newNotification]);
        setTimeout(() => {
            setNotifications((prev) => prev.filter((n) => n.id !== newNotification.id));
        }, 4000);
    };

    // Tracks the currently open layout-editor session so a state
    // change (new client connected, monitor list grew) can be pushed
    // to the editor window without the admin having to close and
    // re-open it. ``preselectClientUid`` is stored here so re-emits
    // keep highlighting the same client.
    const [editorSession, setEditorSession] = useState<{
        open: boolean;
        preselectClientUid?: string;
    }>({open: false});

    // Stable payload builder used by both the initial open and the
    // re-emit effect below. Captures the latest state via closure.
    const buildLayoutEditorPayload = (
        preselectClientUid?: string,
    ): LayoutEditorInitPayload => {
        const rawServer = serverMonitors.length > 0
            ? serverMonitors
            : ([{
                monitor_id: 0,
                min_x: 0,
                min_y: 0,
                max_x: 1920,
                max_y: 1080,
                is_primary: true,
            }] as MonitorInfo[]);

        // Extract ONLY the exact primitive fields we need to absolutely
        // guarantee there are no hidden cyclic dependencies or getters/proxies
        // from the framework's reactivity system.
        const stripMonitor = (m: any) => ({
            monitor_id: m.monitor_id ?? 0,
            min_x: m.min_x ?? 0,
            min_y: m.min_y ?? 0,
            max_x: m.max_x ?? 0,
            max_y: m.max_y ?? 0,
            is_primary: !!m.is_primary,
            name: typeof m.name === 'string' ? m.name : '',
            scaling_factor: m.scaling_factor ?? 1,
        });

        const stripPlacement = (p: any) => ({
            client_uid: p.client_uid ?? '',
            client_monitor_id: p.client_monitor_id ?? 0,
            workspace_x: p.workspace_x ?? 0,
            workspace_y: p.workspace_y ?? 0,
            width: p.width ?? 0,
            height: p.height ?? 0,
        });

        return {
            serverMonitors: rawServer.map(stripMonitor),
            clients: clientManager.clients.map((c) => ({
                uid: c.uid || c.id || '',
                name: c.name || (c.ips ? c.ips.join(', ') : '') || c.id || '',
                monitors: c.monitors ? c.monitors.map(stripMonitor) : [],
            })),
            placements: layoutPlacements.map(stripPlacement),
            preselectClientUid:
                typeof preselectClientUid === 'string'
                    ? preselectClientUid
                    : undefined,
        };
    };

    // Live refresh: whenever the editor is open and the upstream state
    // changes (a freshly approved client just appeared in the manager,
    // its monitor list arrived a tick later, etc.), push a new INIT to
    // the editor window. The editor preserves the user's in-progress
    // placements on re-INIT - only the sidebar / serverMonitors get
    // refreshed. This is what fixes the "approve → editor opens empty,
    // needs reopen to see monitors" bug.
    useEffect(() => {
        if (!editorSession.open) return;
        emit(
            LAYOUT_EDITOR_EVENTS.INIT,
            buildLayoutEditorPayload(editorSession.preselectClientUid),
        ).catch((err) => {
            console.error('Failed to refresh layout editor payload', err);
        });
        // buildLayoutEditorPayload reads from clientManager.clients,
        // serverMonitors and layoutPlacements - all explicit deps below.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [
        editorSession.open,
        editorSession.preselectClientUid,
        clientManager.clients,
        serverMonitors,
        layoutPlacements,
    ]);

    // Opens the layout-editor window (defined statically in
    // tauri.conf.json so we don't need extra create-window permissions),
    // pushes the current state to it once it signals readiness, and
    // listens for the save / cancel reply.
    //
    // ``preselectClientUid`` (optional) tells the editor which client
    // to highlight in the sidebar - used by the approve/add flow so
    // a freshly-onboarded client's monitors are immediately visible.
    const openLayoutEditorWindow = async (preselectClientUid?: string) => {
        const editorWindow = await Window.getByLabel('layout-editor');
        if (!editorWindow) {
            addNotification('error', 'Layout editor window not available');
            return;
        }

        const buildPayload = () => buildLayoutEditorPayload(preselectClientUid);

        // Wire the response listeners BEFORE showing the window so the
        // ready event from the editor (fired as soon as it mounts) is
        // never lost.
        const unlistenFns: UnlistenFn[] = [];
        const cleanup = () => {
            for (const u of unlistenFns) {
                try { u(); } catch { /* ignore */ }
            }
            setEditorSession({open: false});
        };

        unlistenFns.push(
            await listen(LAYOUT_EDITOR_EVENTS.READY, async () => {
                await emit(LAYOUT_EDITOR_EVENTS.INIT, buildPayload());
            }),
        );
        unlistenFns.push(
            await listen<LayoutEditorSavePayload>(
                LAYOUT_EDITOR_EVENTS.SAVE,
                async (event) => {
                    const next = event.payload.placements || [];
                    setLayoutPlacements(next);

                    // Per-client dispatch: each known client gets a
                    // ``SetClientLayout`` call - including those with an
                    // empty resulting list, so the daemon can clear a
                    // previously-set layout. The canonical grouping
                    // key is the client's daemon UID (``c.uid``); the
                    // local fallback ``c.id`` is used only for clients
                    // that haven't paired yet (no daemon UID). The
                    // editor's placements carry the daemon UID via
                    // ``client_uid``, so we look up the client by UID
                    // first and fall back to ``c.id`` only when needed.
                    // Read from ``clientsRef`` (refreshed every render)
                    // instead of the closure's ``clientManager.clients``:
                    // when the editor is auto-opened right after Allow,
                    // the closure's snapshot is the pre-approval list,
                    // and the just-approved client is missing. The ref
                    // guarantees the freshest list at save time, so the
                    // freshly-paired UID resolves and ``setClientLayout``
                    // is called with the daemon's expected identity.
                    const currentClients = clientsRef.current;
                    const groupKey = (c: typeof currentClients[number]) =>
                        c.uid || c.id;
                    const grouped = new Map<string, MonitorPlacement[]>();
                    for (const c of currentClients) {
                        grouped.set(groupKey(c), []);
                    }
                    for (const p of next) {
                        const key = p.client_uid || '';
                        const existing = grouped.get(key) ?? [];
                        existing.push(p);
                        grouped.set(key, existing);
                    }

                    let saved = 0;
                    let failed = 0;
                    for (const [clientKey, placements] of grouped) {
                        const client = currentClients.find(
                            (c) => groupKey(c) === clientKey,
                        );
                        if (!client && placements.length === 0) {
                            // Stale empty entry for a client that's
                            // gone - skip silently, nothing to clear.
                            continue;
                        }
                        try {
                            await setClientLayout(
                                client?.uid || undefined,
                                placements,
                                {
                                    hostname: client?.name,
                                    ipAddress: client?.ips?.[0],
                                },
                            );
                            saved += 1;
                        } catch (err) {
                            failed += 1;
                            console.error(
                                'SetClientLayout failed for',
                                clientKey,
                                err,
                            );
                            addNotification(
                                'error',
                                `Layout rejected for ${client?.name ?? clientKey}`,
                                String(err),
                            );
                        }
                    }
                    if (failed === 0) {
                        addNotification(
                            'info',
                            saved === 1
                                ? 'Layout saved'
                                : `Layout saved (${saved} clients)`,
                        );
                    }
                    cleanup();
                },
            ),
        );
        unlistenFns.push(
            await listen(LAYOUT_EDITOR_EVENTS.CANCEL, () => {
                cleanup();
            }),
        );

        try {
            await editorWindow.show();
            await editorWindow.setFocus();
            // The editor window is created at app boot (visible:false) and
            // emits READY once on mount - by the time the user opens it
            // that emit is long lost, so we push INIT directly. The
            // READY listener above still handles the re-mount case (dev
            // hot-reload etc).
            await emit(LAYOUT_EDITOR_EVENTS.INIT, buildPayload());
            // Mark the session open AFTER the initial INIT so the live
            // re-emit effect (above) takes over for subsequent state
            // changes - e.g. the ``ClientConnected`` event that fires
            // a tick after ``approveClient`` resolves and finally
            // populates ``clientManager.clients`` with the new entry.
            setEditorSession({open: true, preselectClientUid});
        } catch (err) {
            console.error('Failed to show layout editor window', err);
            addNotification('error', 'Failed to open layout editor');
            cleanup();
        }
    };

    const handleToggleClients = () => {
        setShowClients(!showClients);
        setShowSecurity(false);
        setShowOptions(false);
    };

    const handleToggleSecurity = () => {
        setShowSecurity(!showSecurity);
        setShowClients(false);
        setShowOptions(false);
    };

    const handleToggleOptions = () => {
        setShowOptions(!showOptions);
        setShowClients(false);
        setShowSecurity(false);
    };

    // Live countdown for the displayed OTP. Drives the "Expires in Xs" label
    // shown on the inline OTP card under the power button.
    useEffect(() => {
        if (!otp || otpExpiresAt === null) {
            setOtpRemaining(0);
            return;
        }
        const tick = () => {
            const remaining = Math.max(
                0,
                Math.ceil((otpExpiresAt - Date.now()) / 1000)
            );
            setOtpRemaining(remaining);
        };
        tick();
        const id = setInterval(tick, 1000);
        return () => clearInterval(id);
    }, [otp, otpExpiresAt]);

    // Single source of truth for OTP expiration: whoever sets otpExpiresAt
    // triggers exactly one expiry - no race between the pairing path and the
    // manual share path, no double-fire under React StrictMode.
    useEffect(() => {
        if (!otp || otpExpiresAt === null) return;
        const delay = otpExpiresAt - Date.now();
        if (delay <= 0) return;
        const id = setTimeout(() => {
            setOtp('');
            setOtpExpiresAt(null);
            setPairingRequester('');
            addNotification('info', 'OTP Expired');
        }, delay);
        return () => clearTimeout(id);
    }, [otp, otpExpiresAt]);

    const dismissOtp = () => {
        setOtp('');
        setOtpExpiresAt(null);
        setOtpRemaining(0);
        setPairingRequester('');
        setOtpRequested(false);
    };

    useEffect(() => {
        if (!isRunning) return;

        const uptimeInterval = setInterval(() => {
            setUptime(prev => prev + 1);
        }, 1000);

        return () => {
            clearInterval(uptimeInterval);
        };
    }, [isRunning]);

    useEffect(() => {
        if (previousState.current === null) {
            previousState.current = state;
        } else if (JSON.stringify(previousState.current) !== JSON.stringify(state)) {
            previousState.current = state;
        } else {
            return; // No changes detected
        }
        console.log('[Server] State updated', state);
        onStatusChange(state.running);
        setIsRunning(state.running);
        switchTrayIcon(state.running);
        setUid(state.uid);
        setHost(state.host);
        setPort(state.port.toString());
        setRequireSSL(state.ssl_enabled);
        let permissions = parseStreams(state.streams_enabled);
        setEnableMouse(permissions.includes(StreamType.Mouse));
        setEnableKeyboard(permissions.includes(StreamType.Keyboard));
        setEnableClipboard(permissions.includes(StreamType.Clipboard));

        let clients = state.authorized_clients;
        clients.forEach(client => {
            handleClientConnected(client, client.is_connected, false);
        });

        if (state.running) {
            clientEventHandler.cleanup();
            clientEventHandler.setup();

            if (state.start_time) {
                let startDate = new Date(state.start_time);
                let now = new Date();
                setUptime(Math.floor((now.getTime() - startDate.getTime()) / 1000));
            }
        }

    }, [state]);

    const handleClientConnected = (clientData: ClientObj, connected: boolean, notify: boolean = false) => {
        console.log("Processing client connection event:", clientData, connected);
        if (notify) {
            const label = clientData.host_name
                ? clientData.host_name
                : clientData.ip_addresses.join(', ');
            const placementCount = clientData.placements?.length ?? 0;
            const detail =
                placementCount > 0
                    ? `${placementCount} monitor${placementCount === 1 ? '' : 's'} placed`
                    : 'Unplaced - open the Layout Editor to place it';
            addNotification(
                connected ? 'success' : 'warning',
                connected ? 'Client Connected' : 'Client Disconnected',
                `${label} · ${detail}`,
            );
        }
        clientManager.updateClientStatus(clientData, connected);
    };

    function handleClientEventListeners() {

        const setup = () => {
            listenGeneralEvent(EventType.ClientConnected, false, (event) => {
                // Handle client connected event here
                let client_data = event.data as ClientObj;
                handleClientConnected(client_data, true, true);
            }).then(unlisten => {
                listeners.addListenerOnce('client-connected', unlisten);
            });

            listenGeneralEvent(EventType.ClientDisconnected, false, (event) => {
                // Handle client disconnected event here
                let client_data = event.data as ClientObj;
                handleClientConnected(client_data, false, true);
            }).then(unlisten => {
                listeners.addListenerOnce('client-disconnected', unlisten);
            });

            // A client asked us to auto-generate an OTP. Surface it the same
            // way as a manual share: populate the OTP field and toast.
            listenGeneralEvent(EventType.PairingRequested, false, (event) => {
                const info = event.data as PairingRequestInfo | undefined;
                if (!info || !info.otp) return;
                handlePairingRequest(info);
            }).then(unlisten => {
                listeners.addListenerOnce('pairing-requested', unlisten);
            });

            // An unknown client is trying to connect - server is holding the
            // handshake open until we allow or deny via the inline card.
            listenGeneralEvent(EventType.ClientApprovalRequested, false, (event) => {
                const info = event.data as ClientApprovalRequest | undefined;
                if (!info || !info.peer_ip) return;
                handleApprovalRequest(info);
            }).then(unlisten => {
                listeners.addListenerOnce('approval-requested', unlisten);
            });

            // Server signalled the approval is resolved (timeout, second
            // window, etc.). Drop the inline card if it's still up.
            listenGeneralEvent(EventType.ClientApprovalResolved, false, (event) => {
                const info = event.data as ClientApprovalResolved | undefined;
                if (!info || !info.peer_ip) return;
                handleApprovalResolved(info);
            }).then(unlisten => {
                listeners.addListenerOnce('approval-resolved', unlisten);
            });

        };

        const cleanup = () => {
            listeners.removeListener('client-connected');
            listeners.removeListener('client-disconnected');
            listeners.removeListener('pairing-requested');
            listeners.removeListener('approval-requested');
            listeners.removeListener('approval-resolved');
        };

        return {cleanup, setup};
    };

    const handleApprovalRequest = (info: ClientApprovalRequest) => {
        setPendingApprovals((prev) => {
            // Collapse repeated requests from the same IP onto the latest
            // request_id so the card always reflects the most recent attempt.
            const filtered = prev.filter((r) => r.peer_ip !== info.peer_ip);
            return [...filtered, info];
        });
        const who = info.hostname || info.peer_ip;
        addNotification(
            'info',
            'New client wants to connect',
            `${who} is waiting for approval`
        );
    };

    const handleApprovalResolved = (info: ClientApprovalResolved) => {
        setPendingApprovals((prev) =>
            prev.filter((r) => r.peer_ip !== info.peer_ip)
        );
    };

    const handleApproveClient = (req: ClientApprovalRequest) => {
        // Optimistically remove the card; the resolved-event will also remove
        // it but we want immediate UI feedback.
        setPendingApprovals((prev) =>
            prev.filter((r) => r.peer_ip !== req.peer_ip)
        );
        approveClientCommand(req.peer_ip).then(() => {
            // Auto-open the Layout Editor so the admin can position
            // the freshly-approved client's monitors right away.
            // ``req.uid`` is the client's stable UID announced during
            // the pairing handshake - pass it as the preselect hint.
            openLayoutEditorWindow(req.uid || undefined).catch((err) => {
                console.error('Failed to auto-open layout editor', err);
            });
        }).catch((err) => {
            console.error('Error approving client:', err);
            addNotification('error', 'Failed to approve client');
            // Restore the card so the admin can retry.
            setPendingApprovals((prev) =>
                prev.some((r) => r.peer_ip === req.peer_ip) ? prev : [...prev, req]
            );
        });
    };

    const handleDenyClient = (req: ClientApprovalRequest) => {
        setPendingApprovals((prev) =>
            prev.filter((r) => r.peer_ip !== req.peer_ip)
        );
        denyClientCommand(req.peer_ip).catch((err) => {
            console.error('Error denying client:', err);
            addNotification('error', 'Failed to deny client');
        });
    };

    const handlePairingRequest = (info: PairingRequestInfo) => {
        // If we already have an OTP displayed (manual share in progress) and
        // the server reports the OTP was already active, the daemon just
        // mirrored the same code - don't bounce the UI.
        if (otp !== '' && info.was_active) return;

        const who = info.hostname || info.peer_ip || 'a client';
        setOtp(info.otp);
        setPairingRequester(who);
        setOtpExpiresAt(
            info.timeout && info.timeout > 0
                ? Date.now() + info.timeout * 1000
                : null
        );
        setOtpRequested(false);
    };

    // Register Start/Stop server command listeners exactly once for the tab's
    // lifetime. Previously these were re-registered per click, which caused
    // two compounding bugs: a race between the in-callback ``removeListener``
    // and the async ``addListener``, plus ``addListener`` refcounting that
    // dropped the new unlisten - leaking Tauri handlers that fired on
    // subsequent events.
    //
    // Under React StrictMode this useEffect itself runs mount→cleanup→mount.
    // Tauri's ``listen`` API is async, so the cleanup of the first pass runs
    // *before* the promise resolves with the unlisten. The ``cancelled`` flag
    // catches the latecomers: if a promise resolves after cleanup, we
    // immediately call unlisten on it so it can never become a leaked Tauri
    // handler.
    useEffect(() => {
        let cancelled = false;
        const unlisteners: Array<() => void> = [];

        const register = (promise: Promise<() => void>) => {
            promise.then(unlisten => {
                if (cancelled) {
                    unlisten();
                } else {
                    unlisteners.push(unlisten);
                }
            });
        };

        register(listenCommand(EventType.CommandSuccess, CommandType.StartServer, (event) => {
            console.log(`Server started successfully: ${event.message}`);
            const res = event.data?.result;
            setIsRunning(true);
            switchTrayIcon(true);
            setRunningPending(false);
            if (res) {
                addNotification('success', 'Server started', `Listening on ${res.host}:${res.port}`);
                setPort(res.port.toString());
                const start_time = res.start_time;
                if (start_time) {
                    const startDate = new Date(start_time);
                    const now = new Date();
                    setUptime(Math.floor((now.getTime() - startDate.getTime()) / 1000));
                }
            }
            clientEventHandler.setup();
        }));

        register(listenCommand(EventType.CommandError, CommandType.StartServer, (event) => {
            addNotification('error', 'Failed', event.data?.error || '');
            setRunningPending(false);
            onStatusChange(false);
        }));

        register(listenCommand(EventType.CommandSuccess, CommandType.StopServer, (event) => {
            console.log(`Server stopped successfully: ${event.message}`);
            setIsRunning(false);
            clientManager.disconnectAll();
            setUptime(0);
            dismissOtp();
            addNotification('warning', 'Server stopped');
            onStatusChange(false);
            setRunningPending(false);
            switchTrayIcon(false);
            clientEventHandler.cleanup();
        }));

        register(listenCommand(EventType.CommandError, CommandType.StopServer, (event) => {
            addNotification('error', 'Failed to stop server', event.data?.error || '');
            setRunningPending(false);
        }));

        return () => {
            cancelled = true;
            unlisteners.forEach(u => u());
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const handleToggleServer = () => {
        if (!isRunning) {
            setRunningPending(true);
            onStatusChange(true);
            startServer().catch((err) => {
                console.error('Error starting server:', err);
                addNotification('error', 'Failed to start server');
                setRunningPending(false);
                onStatusChange(false);
            });
        } else {
            setRunningPending(true);
            stopServer().catch((err) => {
                console.error('Error stopping server:', err);
                addNotification('error', 'Failed to stop server');
                setRunningPending(false);
            });
        }
    };

    const generateOtp = () => {
        if (otp !== '') return;
        if (otpRequested) return;

        listenCommand(EventType.CommandSuccess, CommandType.ShareCertificate, (event) => {
            console.log(`Certificate shared successfully`, event);
            let result = event.data?.result as OtpInfo;
            if (result && result.otp) {
                setOtp(result.otp);
                setPairingRequester('');
                setOtpExpiresAt(
                    result.timeout && result.timeout > 0
                        ? Date.now() + result.timeout * 1000
                        : null
                );
            } else {
                addNotification('error', 'Failed to generate OTP');
            }

            setOtpRequested(false);
            listeners.removeListener('share-certificate');
        }).then(unlisten => {
            listeners.addListener('share-certificate', unlisten);
        });

        shareCertificate(otpTimeout).catch((err) => {
            console.error('Error sharing certificate:', err);
            addNotification('error', 'Failed to share certificate');
            dismissOtp();
        });
        setOtpRequested(true);
    };

    /**
     * Try to confirm the current input as a tag (IP or hostname).
     * Returns true if a tag was added.
     */
    const confirmIpTag = (value?: string): boolean => {
        const raw = (value ?? clientIpInput).trim();
        if (!raw) return false;

        // Accept valid IPs directly
        if (isValidIpAddress(raw)) {
            if (clientIpTags.includes(raw)) {
                addNotification('warning', 'IP already added');
                setClientIpInput('');
                return false;
            }
            setClientIpTags(prev => [...prev, raw]);
            setClientIpInput('');
            return true;
        }

        // Non-IP entry → treat as hostname (allow max 1)
        const existingHostname = clientIpTags.find(t => !isValidIpAddress(t));
        if (existingHostname) {
            addNotification('warning', 'Hostname already set');
            setClientIpInput('');
            return false;
        }

        setClientIpTags(prev => [...prev, raw]);
        setClientIpInput('');
        return true;
    };

    const removeIpTag = (index: number) => {
        setClientIpTags(prev => prev.filter((_, i) => i !== index));
    };

    const handleIpInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'Enter' || e.key === ',' || e.key === 'Tab') {
            e.preventDefault();
            confirmIpTag();
        }
        // Backspace on empty input removes last tag
        if (e.key === 'Backspace' && clientIpInput === '' && clientIpTags.length > 0) {
            setClientIpTags(prev => prev.slice(0, -1));
        }
    };

    const addClient = () => {
        if (isAddingClient) {
            return;
        }

        // Confirm any pending input before submitting
        let tags = [...clientIpTags];
        const pendingRaw = clientIpInput.trim();
        if (pendingRaw) {
            if (isValidIpAddress(pendingRaw)) {
                if (!tags.includes(pendingRaw)) tags.push(pendingRaw);
            } else if (!tags.some(t => !isValidIpAddress(t))) {
                // Add as hostname only if no hostname tag exists yet
                tags.push(pendingRaw);
            }
        }

        if (tags.length === 0) {
            addNotification('error', 'Add a hostname or at least one IP');
            return;
        }

        // Separate hostname (first non-IP tag) from IPs
        const ips = tags.filter(isValidIpAddress);
        const hostname = tags.find(t => !isValidIpAddress(t)) || '';

        // Check if a client with any of the same IPs or hostname already exists
        let existing = clientManager.clients.find(c =>
            ips.length > 0
                ? ips.some(ip => c.ips?.includes(ip))
                : hostname !== '' ? c.name === hostname : false
        );
        if (existing) {
            addNotification('error', 'Client already exists');
            return;
        }

        setIsAddingClient(true)

        listenCommand(EventType.CommandSuccess, CommandType.AddClient, (event) => {
            console.log(`Client added successfully: ${event.message}`);
            let result = event.data?.result as ClientEditObj;
            if (result) {
                addNotification(
                    'info',
                    'Client added',
                    `${hostname || ips.join(', ')} - open the Layout Editor to place its monitors`,
                );
                setClientIpTags([]);
                setClientIpInput('');

                clientManager.addClient(hostname, ips);

                // Auto-open the Layout Editor so the admin can place
                // the freshly-added client's monitors immediately.
                // The daemon may not yet have echoed back the client's
                // monitor list; the editor still highlights the row
                // and will receive the placements via the next status
                // tick.
                const newClientUid =
                    clientManager.clients.find(c => c.name === hostname
                        || (c.ips ?? []).some(ip => ips.includes(ip)))?.uid;
                openLayoutEditorWindow(newClientUid).catch((err) => {
                    console.error('Failed to auto-open layout editor', err);
                });

                listeners.removeListener('add-client');
                listeners.removeListener('add-client-error');
            }
            setIsAddingClient(false);
        }).then(unlisten => {
            listeners.addListenerOnce('add-client', unlisten);
        });

        listenCommand(EventType.CommandError, CommandType.AddClient, (event) => {
            addNotification('error', 'Failed to add client', event.data?.error || '');
            setIsAddingClient(false);
            listeners.removeListener('add-client-error');
            listeners.removeListener('add-client');
        }).then(unlisten => {
            listeners.addListenerOnce('add-client-error', unlisten);
        });

        addClientCommand(hostname, ips.length > 0 ? ips : []).catch((err) => {
            console.error('Error adding client:', err);
            addNotification('error', err.toString());
            setIsAddingClient(false);
            listeners.forceRemoveListener('add-client');
            listeners.forceRemoveListener('add-client-error');
        });

    };

    const removeClient = (id: string) => {
        const client = clientManager.clients.find(c => c.id === id);

        listenCommand(EventType.CommandSuccess, CommandType.RemoveClient, (event) => {
            console.log(`Client removed successfully: ${event.message}`);
            clientManager.removeClient(id);
            addNotification('info', `${client?.name || client?.ips?.join(', ')} removed`);
            listeners.removeListener('remove-client');
            listeners.removeListener('remove-client-error');
        }).then(unlisten => {
            listeners.addListener('remove-client', unlisten);
        });

        listenCommand(EventType.CommandError, CommandType.RemoveClient, (event) => {
            addNotification('error', 'Failed to remove client', event.data?.error || '');
            listeners.removeListener('remove-client-error');
            listeners.removeListener('remove-client');
        }).then(unlisten => {
            listeners.addListener('remove-client-error', unlisten);
        });

        removeClientCommand(client?.name || '', client?.ips?.[0] || '').catch((err) => {
            console.error('Error removing client:', err);
            addNotification('error', err.toString());
            listeners.forceRemoveListener('remove-client');
            listeners.forceRemoveListener('remove-client-error');
        });
    };

    const formatUptime = (seconds: number) => {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = seconds % 60;
        return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    };

    const handleSaveOptions = (hostValue: string, portValue: string, sslEnabledValue: boolean, save_feedback: boolean = true) => {
        console.log('Saving options:', {host: hostValue, port: portValue, sslEnabled: sslEnabledValue});

        if (save_feedback) {
            listenCommand(EventType.CommandSuccess, CommandType.SetServerConfig, (event) => {
                console.log(`Server config saved successfully: ${event.message}`);
                addNotification('success', 'Options saved');
                listeners.removeListener('set-server-config');
            }).then(unlisten => {
                listeners.addListenerOnce('set-server-config', unlisten);
            });
        }

        listenCommand(EventType.CommandError, CommandType.SetServerConfig, (event) => {
            addNotification('error', 'Failed to save options', event.data?.error || '');
            listeners.removeListener('set-server-config-error');
        }).then(unlisten => {
            listeners.addListenerOnce('set-server-config-error', unlisten);
        });

        const portNum = parseInt(portValue, 10);
        saveServerConfig(hostValue, portNum, sslEnabledValue).catch((err) => {
            console.error('Error saving options:', err);
            addNotification('error', 'Failed to save options');
            listeners.forceRemoveListener('set-server-config');
            listeners.forceRemoveListener('set-server-config-error');
        });

    };

    const scheduleOptionsSave = (hostValue: string, portValue: string, sslEnabledValue: boolean) => {
        // Clear existing timeout
        if (saveOptionsTimeoutRef.current) {
            clearTimeout(saveOptionsTimeoutRef.current);
        }

        // Schedule new save after inactivity
        saveOptionsTimeoutRef.current = setTimeout(() => {
            handleSaveOptions(hostValue, portValue, sslEnabledValue, false);
        }, 100);
    };

    return (
        <div className="space-y-5">
            {/* Power Button */}
            <PowerButton
                status={runningPending ? 'pending' : isRunning ? 'running' : 'stopped'}
                onClick={handleToggleServer}
                stoppedLabel="Server Stopped"
                runningLabel="Server Running"
                uid={isRunning ? uid : undefined}
            />

            {/* OTP Display Panel - mirrors the client-side OtpInputPanel
                location. Shows the OTP prominently right under the power
                button whenever one is active, regardless of whether it came
                from a manual "Share Certificate" or an auto pairing request. */}
            <AnimatePresence>
                {otp && (
                    <motion.div
                        ref={otpFocus}
                        initial={{opacity: 0, y: -20, scale: 0.95}}
                        animate={{opacity: 1, y: 0, scale: 1}}
                        exit={{opacity: 0, y: -20, scale: 0.95}}
                        transition={{duration: 0.3, ease: 'easeOut'}}
                        className="p-5 rounded-xl border-2 backdrop-blur-sm"
                        style={{
                            backgroundColor: 'var(--app-card-bg)',
                            borderColor: 'var(--app-primary)',
                            boxShadow: '0 8px 32px rgba(0, 0, 0, 0.12)',
                        }}
                    >
                        <div className="flex items-center gap-3 mb-3">
                            <motion.div
                                animate={{scale: [1, 1.1, 1]}}
                                transition={{
                                    duration: 2,
                                    repeat: Infinity,
                                    ease: 'easeInOut',
                                }}
                                className="w-12 h-12 rounded-lg flex items-center justify-center"
                                style={{backgroundColor: 'var(--app-primary)'}}
                            >
                                <Key size={24} style={{color: 'white'}}/>
                            </motion.div>
                            <div className="flex-1">
                                <h3 className="text-base font-bold"
                                    style={{color: 'var(--app-text-primary)'}}
                                >
                                    {pairingRequester
                                        ? `Pairing request from ${pairingRequester}`
                                        : 'Share Certificate'}
                                </h3>
                                <p className="text-xs mt-1"
                                   style={{color: 'var(--app-text-muted)'}}
                                >
                                    {pairingRequester
                                        ? 'Share this code with the client'
                                        : 'Provide this code to clients'}
                                </p>
                            </div>
                            <motion.button
                                whileHover={{scale: 1.1}}
                                whileTap={{scale: 0.95}}
                                onClick={dismissOtp}
                                className="w-8 h-8 rounded-lg flex items-center justify-center cursor-pointer"
                                style={{
                                    backgroundColor: 'var(--app-bg-secondary)',
                                    color: 'var(--app-text-muted)',
                                }}
                                aria-label="Dismiss OTP"
                            >
                                <X size={16}/>
                            </motion.button>
                        </div>

                        <div
                            className="text-center p-4 rounded-lg border"
                            style={{
                                backgroundColor: 'var(--app-input-bg)',
                                borderColor: 'var(--app-primary)',
                            }}
                        >
                            <p
                                className="text-3xl font-bold tracking-widest select-all cursor-pointer"
                                style={{color: 'var(--app-primary-light)'}}
                                onClick={() => {
                                    navigator.clipboard?.writeText(otp).then(
                                        () => addNotification('info', 'OTP copied'),
                                        () => {}
                                    );
                                }}
                                title="Click to copy"
                            >
                                {otp}
                            </p>
                            <p className="text-sm mt-2"
                               style={{color: 'var(--app-text-muted)'}}
                            >
                                {otpRemaining > 0
                                    ? `Expires in ${otpRemaining}s`
                                    : otpExpiresAt
                                        ? 'Expired'
                                        : `Valid for ${otpTimeout}s`}
                            </p>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Pending client-approval prompts. One card per unknown
                client attempting to connect; the admin clicks Allow or
                Deny - no position picker, since approved clients are
                placed visually in the Layout Editor (auto-opened post-
                approval). The handshake on the server is held open
                until one of the buttons is pressed (or the request
                times out, in which case the resolved event removes
                the card). */}
            <AnimatePresence>
                {pendingApprovals.map((req) => (
                    <PendingApprovalCard
                        key={`${req.peer_ip}-${req.request_id}`}
                        request={req}
                        onApprove={() => handleApproveClient(req)}
                        onDeny={() => handleDenyClient(req)}
                    />
                ))}
            </AnimatePresence>

            {/* Inline Notifications */}
            <InlineNotification notifications={notifications}/>

            {/* Server Information - Horizontal Layout */}
            <motion.div
                initial={{opacity: 0}}
                animate={{opacity: 1}}
                transition={{delay: 0.2}}
                className="flex gap-2"
            >
                <motion.div
                    whileHover={{scale: 1.02}}
                    className="flex-1 flex items-center gap-3 p-4 rounded-lg border backdrop-blur-sm"
                    style={{
                        backgroundColor: 'var(--app-card-bg)',
                        borderColor: 'var(--app-card-border)'
                    }}
                >
                    <div className="w-10 h-10 rounded-lg flex items-center justify-center"
                         style={{backgroundColor: 'var(--app-primary)'}}>
                        <Users size={20} style={{color: 'white'}}/>
                    </div>
                    <div className="flex-1">
                        <motion.div
                            key={clientManager.connectedCount}
                            initial={{scale: 1.3}}
                            animate={{scale: 1}}
                            className="text-xl font-bold"
                            style={{color: 'var(--app-text-primary)'}}
                        >
                            {clientManager.connectedCount}
                        </motion.div>
                        <div className="text-xs" style={{color: 'var(--app-text-muted)'}}>Connected</div>
                    </div>
                </motion.div>

                <motion.div
                    whileHover={{scale: 1.02}}
                    className="flex-1 flex items-center gap-3 p-4 rounded-lg border backdrop-blur-sm"
                    style={{
                        backgroundColor: 'var(--app-card-bg)',
                        borderColor: 'var(--app-card-border)'
                    }}
                >
                    <div className="w-10 h-10 rounded-lg flex items-center justify-center"
                         style={{backgroundColor: 'var(--app-primary)'}}>
                        <Activity size={20} style={{color: 'white'}}/>
                    </div>
                    <div className="flex-1">
                        <div className="text-xl font-bold" style={{color: 'var(--app-text-primary)'}}>
                            {formatUptime(uptime)}
                        </div>
                        <div className="text-xs" style={{color: 'var(--app-text-muted)'}}>Uptime</div>
                    </div>
                </motion.div>
            </motion.div>

            {/* Active Permissions Panel - Always Visible */}
            <PermissionsPanel
                enableMouse={enableMouse}
                enableKeyboard={enableKeyboard}
                enableClipboard={enableClipboard}
                setEnableMouse={setEnableMouse}
                setEnableKeyboard={setEnableKeyboard}
                setEnableClipboard={setEnableClipboard}
                addNotification={addNotification}
                listeners={listeners}
                disableAllStreams={false}
            />

            {/* Action Buttons */}
            <div className="grid grid-cols-4 gap-2">
                <ActionButton onClick={handleToggleClients} clicked={showClients}>
                    <Users size={20}/>
                    <span className="text-xs">Clients</span>
                </ActionButton>

                <ActionButton onClick={openLayoutEditorWindow} clicked={false}>
                    <LayoutIcon size={20}/>
                    <span className="text-xs">Layout</span>
                </ActionButton>

                <ActionButton onClick={handleToggleSecurity} clicked={showSecurity}>
                    <Lock size={20}/>
                    <span className="text-xs">Security</span>
                </ActionButton>

                <ActionButton onClick={handleToggleOptions} clicked={showOptions}>
                    <Settings size={20}/>
                    <span className="text-xs">Options</span>
                </ActionButton>
            </div>

            {/* Clients Section */}
            <AnimatePresence>
                {showClients && (
                    <motion.div
                        initial={{opacity: 0, height: 0}}
                        animate={{opacity: 1, height: 'auto'}}
                        exit={{opacity: 0, height: 0}}
                        transition={{duration: 0.3}}
                        className="overflow-hidden"
                    >
                        <div className="space-y-4 p-4 rounded-lg border overflow-visible"
                             style={{
                                 backgroundColor: 'var(--app-card-bg)',
                                 borderColor: 'var(--app-card-border)'
                             }}
                        >
                            <h3 className="font-semibold flex items-center gap-2"
                                style={{color: 'var(--app-text-primary)'}}
                            >
                                <Users size={18}/>
                                Manage Clients
                            </h3>

                            <div className="space-y-2">
                                <AnimatePresence mode="wait">
                                    {clientIpTags.length === 0 && clientIpInput === '' && (
                                        <motion.div
                                            initial={{opacity: 0, height: 0, scale: 0.95}}
                                            animate={{opacity: 1, height: 'auto', scale: 1}}
                                            exit={{opacity: 0, height: 0, scale: 0.95}}
                                            transition={{duration: 0.2, ease: 'easeOut'}}
                                            className="flex items-start gap-3 p-3 rounded-lg border overflow-hidden"
                                            style={{
                                                backgroundColor: 'var(--app-bg-secondary)',
                                                borderColor: 'var(--app-primary)',
                                                borderWidth: '1px'
                                            }}
                                        >
                                            <Info size={18}
                                                  style={{color: 'var(--app-primary)', marginTop: '2px', flexShrink: 0}}/>
                                            <p className="text-xs leading-relaxed"
                                               style={{color: 'var(--app-text-secondary)'}}>
                                                Enter one or more <strong style={{color: 'var(--app-text-primary)'}}>IP addresses</strong> and optionally one <strong style={{color: 'var(--app-text-primary)'}}>hostname</strong> to identify this client. Confirm each entry with <kbd className="px-1 py-0.5 rounded font-mono" style={{backgroundColor: 'var(--app-bg-tertiary)', border: '1px solid var(--app-border)'}}>Enter</kbd>, <kbd className="px-1 py-0.5 rounded font-mono" style={{backgroundColor: 'var(--app-bg-tertiary)', border: '1px solid var(--app-border)'}}>Space</kbd> or <kbd className="px-1 py-0.5 rounded font-mono" style={{backgroundColor: 'var(--app-bg-tertiary)', border: '1px solid var(--app-border)'}}>,</kbd>
                                            </p>
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                                <div
                                    className="flex flex-wrap items-center gap-1.5 cursor-text app-input"
                                    onClick={() => ipInputRef.current?.focus()}
                                >
                                    <AnimatePresence mode="popLayout">
                                        {clientIpTags.map((tag, index) => (
                                            <motion.span
                                                key={tag}
                                                initial={{opacity: 0, scale: 0.8}}
                                                animate={{opacity: 1, scale: 1}}
                                                exit={{opacity: 0, scale: 0.8}}
                                                transition={{duration: 0.15}}
                                                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-sm"
                                                style={{
                                                    backgroundColor: isValidIpAddress(tag) ? 'var(--app-primary)' : 'var(--app-bg-tertiary)',
                                                    color: isValidIpAddress(tag) ? 'white' : 'var(--app-text-primary)',
                                                    border: isValidIpAddress(tag) ? 'none' : '1px solid var(--app-border)',
                                                }}
                                            >
                                                {tag}
                                                <button
                                                    type="button"
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        removeIpTag(index);
                                                    }}
                                                    className="cursor-pointer p-0 leading-none rounded-full transition-opacity hover:opacity-70"
                                                    style={{color: 'inherit'}}
                                                >
                                                    <X size={12}/>
                                                </button>
                                            </motion.span>
                                        ))}
                                    </AnimatePresence>
                                    <input
                                        ref={ipInputRef}
                                        type="text"
                                        placeholder={clientIpTags.length === 0 ? 'IP address(es) or hostname' : clientIpTags.some(t => !isValidIpAddress(t)) ? 'Add IP...' : 'Add IP or hostname...'}
                                        value={clientIpInput}
                                        onChange={(e) => {
                                            // Auto-confirm on comma or space after a valid IP
                                            const val = e.target.value;
                                            if (val.endsWith(',') || val.endsWith(' ')) {
                                                const cleaned = val.slice(0, -1).trim();
                                                if (cleaned && isValidIpAddress(cleaned)) {
                                                    confirmIpTag(cleaned);
                                                    return;
                                                }
                                            }
                                            setClientIpInput(val);
                                        }}
                                        onKeyDown={handleIpInputKeyDown}
                                        onBlur={() => {
                                            // On blur, try to confirm pending input
                                            if (clientIpInput.trim()) {
                                                confirmIpTag();
                                            }
                                        }}
                                        className="flex-1 min-w-[120px] bg-transparent border-none outline-none"
                                    />
                                </div>
                                {/* Position picker removed - clients are placed
                                    free-form in the Layout Editor (auto-opens
                                    after add/approve). The 4-client cap is gone:
                                    any number of clients can be onboarded. */}

                                <motion.button
                                    whileHover={{scale: 1.02}}
                                    whileTap={{scale: 0.98}}
                                    onClick={addClient}
                                    disabled={isAddingClient}
                                    className="cursor-pointer w-full p-3 rounded-lg transition-all flex items-center justify-center gap-2"
                                    style={{
                                        backgroundColor: isAddingClient ? 'var(--app-bg-tertiary)' : 'var(--app-primary)',
                                        color: 'white',
                                        opacity: isAddingClient ? 0.6 : 1,
                                        cursor: isAddingClient ? 'not-allowed' : 'pointer'
                                    }}
                                >
                                    <Plus size={20}/>
                                    Add Client
                                </motion.button>
                            </div>

                            <ScrollArea
                                extraPadding='pl-2.5'
                                className="space-y-2 max-h-60 overflow-y-auto custom-scrollbar w-full"
                            >
                                {clientManager.clients.map(client => (
                                    <motion.div
                                        key={client.id}
                                        initial={{opacity: 0, x: -20}}
                                        animate={{opacity: 1, x: 0}}
                                        exit={{opacity: 0, x: 20}}
                                        className="p-3 rounded-lg border grid grid-cols-[1fr_auto_auto] gap-3 items-center"
                                        style={{
                                            backgroundColor: client.status === 'online' ? 'var(--app-success-bg)' : 'var(--app-input-bg)',
                                            borderColor: client.status === 'online' ? 'var(--app-success)' : 'var(--app-input-bg)'
                                        }}
                                    >
                                        {/* Client Info */}
                                        <div className="min-w-0">
                                            <p className="font-semibold truncate cursor-help"
                                               style={{color: 'var(--app-text-primary)'}}
                                               title={client.name}
                                            >{client.name}</p>
                                            <p className="text-sm truncate cursor-help"
                                               style={{color: 'var(--app-text-muted)'}}
                                               title={client.ips?.join(', ')}
                                            >{client.ips?.join(', ')}</p>
                                        </div>

                                        {/* Badges Section */}
                                        <div className="flex flex-col items-end gap-2">
                                            <div className="flex items-center gap-2">
                                                {(() => {
                                                    const placementCount = client.placements?.length ?? 0;
                                                    const placed = placementCount > 0;
                                                    return (
                                                        <button
                                                            onClick={() =>
                                                                openLayoutEditorWindow(
                                                                    client.uid,
                                                                ).catch((err) =>
                                                                    console.error(
                                                                        'Failed to open layout editor',
                                                                        err,
                                                                    ),
                                                                )
                                                            }
                                                            className="px-2 py-1 text-xs rounded min-w-[96px] text-center cursor-pointer"
                                                            style={{
                                                                backgroundColor: placed
                                                                    ? 'var(--app-success-bg)'
                                                                    : 'var(--app-warning-bg, var(--app-bg-tertiary))',
                                                                color: placed
                                                                    ? 'var(--app-success)'
                                                                    : 'var(--app-warning, var(--app-text-muted))',
                                                                border: `1px solid ${
                                                                    placed
                                                                        ? 'var(--app-success)'
                                                                        : 'var(--app-warning, var(--app-input-border))'
                                                                }`,
                                                            }}
                                                            title={
                                                                placed
                                                                    ? `Open Layout Editor for ${client.name}`
                                                                    : `Unplaced - open Layout Editor to place ${client.name}`
                                                            }
                                                        >
                                                            {placed
                                                                ? `${placementCount} placed`
                                                                : 'Unplaced'}
                                                        </button>
                                                    );
                                                })()}
                                                <span className="px-2 py-1 rounded text-xs min-w-[56px] text-center"
                                                      style={{
                                                          backgroundColor: client.status === 'online' ? 'var(--app-input-bg)' : 'var(--app-bg-tertiary)',
                                                          color: client.status === 'online' ? 'var(--app-success)' : 'var(--app-text-muted)'
                                                      }}
                                                >
                          {client.status.charAt(0).toUpperCase() + client.status.slice(1)}
                        </span>
                                            </div>
                                            {client.uid && (
                                                <CopyableBadge
                                                    key={client.uid}
                                                    fullText={client.uid}
                                                    displayText={abbreviateText(client.uid, 4, 4)}
                                                    label=""
                                                    titleText={`Click to copy Client UID: ${client.uid}`}
                                                    style={
                                                        {
                                                            width: "100%",
                                                            justifyContent: "center"
                                                        }
                                                    }
                                                />
                                            )}
                                        </div>

                                        {/* Delete Button */}
                                        <motion.button
                                            whileHover={{scale: 1.1}}
                                            whileTap={{scale: 0.9}}
                                            onClick={() => removeClient(client.id)}
                                            className="cursor-pointer p-2 transition-colors"
                                            style={{color: 'var(--app-danger)'}}
                                        >
                                            <Trash2 size={16}/>
                                        </motion.button>
                                    </motion.div>
                                ))}
                            </ScrollArea>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Security Section */}
            <AnimatePresence>
                {showSecurity && (
                    <motion.div
                        initial={{opacity: 0, height: 0}}
                        animate={{opacity: 1, height: 'auto'}}
                        exit={{opacity: 0, height: 0}}
                        transition={{duration: 0.3}}
                        className="overflow-hidden"
                    >
                        <div className="space-y-4 p-4 rounded-lg border"
                             style={{
                                 backgroundColor: 'var(--app-card-bg)',
                                 borderColor: 'var(--app-card-border)'
                             }}
                        >
                            <h3 className="font-semibold flex items-center gap-2"
                                style={{color: 'var(--app-text-primary)'}}
                            >
                                <Shield size={18}/>
                                Security Settings
                            </h3>

                            <div className="space-y-3">
                                <div className="flex items-center justify-between">
                                    <label className="flex items-center gap-2"
                                           style={{color: 'var(--app-text-primary)'}}
                                    >
                                        <Lock size={18}/>
                                        <span>Secure connection</span>
                                    </label>
                                    <Switch
                                        id="requireSSL"
                                        checked={requireSSL}
                                        disabled={otp !== '' || otpRequested}
                                        onCheckedChange={(checked) => {
                                            if (otp !== '' || otpRequested) return;
                                            setRequireSSL(checked);
                                            handleSaveOptions(host, port, checked, false);
                                        }}
                                    />
                                </div>
                            </div>

                            {requireSSL && isRunning && (
                                <motion.div
                                    initial={{opacity: 0, scale: 0.95}}
                                    animate={{opacity: 1, scale: 1}}
                                    className="pt-4 space-y-3"
                                    style={{borderTop: '1px solid var(--app-border)'}}
                                >
                                    <div className="flex items-center justify-between">
                                        <span style={{color: 'var(--app-text-primary)'}}>One-Time Password</span>
                                        <motion.button
                                            whileHover={otp !== '' || otpRequested ? undefined : {scale: 1.05}}
                                            whileTap={{scale: 0.95}}
                                            disabled={otp !== '' || otpRequested}
                                            onClick={generateOtp}
                                            className="px-4 py-2 rounded-lg transition-all flex items-center gap-2 disabled:opacity-50"
                                            style={{
                                                cursor: otp !== '' || otpRequested ? '' : 'pointer',
                                                backgroundColor: otp !== '' || otpRequested ? 'var(--app-bg-secondary)' : 'var(--app-primary)',
                                                color: otp !== '' || otpRequested ? 'var(--app-text-muted)' : 'white'
                                            }}
                                        >
                                            <Key size={16}/>
                                            Generate
                                        </motion.button>
                                    </div>
                                </motion.div>
                            )}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Options Panel */}
            <AnimatePresence>
                {showOptions && (
                    <motion.div
                        initial={{opacity: 0, height: 0}}
                        animate={{opacity: 1, height: 'auto'}}
                        exit={{opacity: 0, height: 0}}
                        transition={{duration: 0.3}}
                        className="overflow-hidden"
                    >
                        <div className="space-y-4 p-4 rounded-lg border"
                             style={{
                                 backgroundColor: 'var(--app-card-bg)',
                                 borderColor: 'var(--app-card-border)'
                             }}
                        >
                            <h3 className="font-semibold flex items-center gap-2"
                                style={{color: 'var(--app-text-primary)'}}
                            >
                                <Settings size={18}/>
                                Server Options
                            </h3>

                            <div>
                                <label className="block mb-2 font-semibold"
                                       style={{color: 'var(--app-text-primary)'}}
                                >Host</label>
                                <input
                                    type="text"
                                    value={host}
                                    onChange={(e) => {
                                        const newHost = e.target.value;
                                        setHost(newHost);
                                        scheduleOptionsSave(newHost, port, requireSSL);
                                    }}
                                    className="app-input"
                                    disabled={isRunning}
                                />
                            </div>

                            <div>
                                <label className="block mb-2 font-semibold"
                                       style={{color: 'var(--app-text-primary)'}}
                                >Port</label>
                                <input
                                    type="text"
                                    value={port}
                                    onChange={(e) => {
                                        const newPort = e.target.value;
                                        setPort(newPort);
                                        scheduleOptionsSave(host, newPort, requireSSL);
                                    }}
                                    className="app-input"
                                    disabled={isRunning}
                                />
                            </div>

                            <div>
                                <label className="block mb-2 font-semibold"
                                       style={{color: 'var(--app-text-primary)'}}
                                >OTP Timeout (seconds)</label>
                                <input
                                    type="number"
                                    value={otpTimeout}
                                    onChange={(e) => {
                                        setOtpTimeout(parseInt(e.target.value));
                                    }}
                                    className="app-input"
                                />
                            </div>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}