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
import {CommandType, EventType, ServiceStatus} from '../api/Interface';
import {listenCommand} from '../api/Listener';
import {chooseService, getStatus} from '../api/Sender';
import {ActionType} from '../store/actions';
import {useAppDispatch} from './redux';

type Mode = 'client' | 'server';
type Phase = 'inspect' | 'choose' | 'hydrate' | 'recover' | 'ready';

/** IPC invocation only acknowledges sending: readiness requires a daemon reply. */
export function useDaemonSync() {
    const dispatch = useAppDispatch();
    const [mode, setMode] = useState<Mode>('client');
    const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
    const [error, setError] = useState('');
    const [hasState, setHasState] = useState(false);
    const cachedModes = useRef(new Set<Mode>());
    const [registrationAttempt, setRegistrationAttempt] = useState(0);
    const actions = useRef({retry: () => {}, refresh: () => {}, changeMode: (_mode: Mode) => {}});

    useEffect(() => {
        let disposed = false;
        let registered = false;
        let phase: Phase = 'inspect';
        let currentMode: Mode = 'client';
        let target: Mode = 'client';
        let hydrated = false;
        let pendingStatus = false;
        let queuedMode: Mode | null = null;
        let choiceError = '';
        let timer: ReturnType<typeof setTimeout> | undefined;
        const unlisteners: (() => void)[] = [];

        const fail = (message: string) => {
            if (disposed) return;
            clearTimeout(timer);
            setError(message);
            setStatus('error');
        };
        const armTimeout = () => {
            clearTimeout(timer);
            timer = setTimeout(() => fail('The daemon did not finish loading the configuration. Please retry.'), 10000);
        };
        const requestStatus = () => {
            if (disposed || !registered || pendingStatus || phase === 'choose') return;
            pendingStatus = true;
            if (phase === 'ready') armTimeout();
            getStatus().catch(err => {
                if (disposed) return;
                pendingStatus = false;
                fail(String(err));
            });
        };
        const showTarget = (next: Mode) => {
            setMode(next);
            setHasState(cachedModes.current.has(next));
            setStatus('loading');
            setError('');
        };
        const beginChoice = (next: Mode) => {
            target = next;
            phase = 'choose';
            showTarget(next);
            armTimeout();
            chooseService(next).catch(err => {
                if (disposed || phase !== 'choose') return;
                choiceError = `Cannot switch to ${next} mode: ${String(err)}`;
                phase = 'recover';
                requestStatus();
            });
        };
        const adopt = (next: Mode) => {
            currentMode = next;
            hydrated = true;
            phase = 'ready';
            clearTimeout(timer);
            setMode(next);
            setHasState(true);
            setStatus('ready');
            setError('');
        };
        const register = async (promise: Promise<() => void>) => {
            const unlisten = await promise;
            if (disposed) unlisten();
            else unlisteners.push(unlisten);
        };

        actions.current = {
            retry: () => {
                if (disposed) return;
                if (!registered) {
                    setStatus('loading');
                    setError('');
                    setRegistrationAttempt(value => value + 1);
                    return;
                }
                // No request IDs exist in this protocol. A retry always inspects
                // current truth first; late STATUS replies are valid snapshots.
                pendingStatus = false;
                queuedMode = null;
                choiceError = '';
                phase = 'inspect';
                setStatus('loading');
                setError('');
                armTimeout();
                requestStatus();
            },
            refresh: () => {
                if (phase === 'ready') requestStatus();
            },
            changeMode: next => {
                if (phase !== 'ready' || next === currentMode || disposed) return;
                if (pendingStatus) {
                    queuedMode = next;
                    showTarget(next);
                    armTimeout();
                } else beginChoice(next);
            },
        };

        armTimeout();
        Promise.all([
            register(listenCommand(EventType.CommandSuccess, CommandType.Status, event => {
                if (disposed || !pendingStatus) return;
                pendingStatus = false;
                const result = event.data?.result as ServiceStatus | undefined;
                if (!result || typeof result !== 'object') {
                    fail('The daemon returned an invalid status. Please retry.');
                    return;
                }
                // Only daemon snapshots populate the cache; Redux defaults do not.
                if (result.server_info) {
                    dispatch({type: ActionType.SERVER_STATE, payload: result.server_info});
                    cachedModes.current.add('server');
                }
                if (result.client_info) {
                    dispatch({type: ActionType.CLIENT_STATE, payload: result.client_info});
                    cachedModes.current.add('client');
                }
                if (queuedMode) {
                    const next = queuedMode;
                    queuedMode = null;
                    beginChoice(next);
                    return;
                }
                const active: Mode | undefined = result.server_info?.running ? 'server'
                    : result.client_info?.running ? 'client' : undefined;
                if (active) {
                    adopt(active);
                } else if (phase === 'inspect') {
                    beginChoice(hydrated ? currentMode : 'client');
                } else if (phase === 'recover') {
                    if (hydrated) {
                        phase = 'ready';
                        clearTimeout(timer);
                        setMode(currentMode);
                        setHasState(cachedModes.current.has(currentMode));
                        setStatus('ready');
                        setError(choiceError);
                    } else fail(choiceError || 'Unable to initialize the service. Please retry.');
                } else {
                    const next = phase === 'hydrate' ? target : currentMode;
                    if (next === 'client' ? result.client_info : result.server_info) adopt(next);
                    else fail('The daemon has not returned the selected service configuration. Please retry.');
                }
            })),
            register(listenCommand(EventType.CommandError, CommandType.Status, event => {
                if (disposed || !pendingStatus) return;
                pendingStatus = false;
                fail(event.data?.error || 'Could not load the configuration.');
            })),
            register(listenCommand(EventType.CommandSuccess, CommandType.ServiceChoice, event => {
                if (disposed || phase !== 'choose') return;
                if (event.message?.toLowerCase() !== target) return;
                if (cachedModes.current.has(target)) {
                    // The service choice is confirmed. Render the saved snapshot
                    // immediately and refresh it without a loading transition.
                    currentMode = target;
                    hydrated = true;
                    phase = 'ready';
                    setMode(target);
                    setHasState(true);
                    setStatus('ready');
                } else phase = 'hydrate';
                requestStatus();
            })),
            register(listenCommand(EventType.CommandError, CommandType.ServiceChoice, event => {
                if (disposed || phase !== 'choose') return;
                choiceError = `Cannot switch to ${target} mode: ${event.data?.error || 'Unknown error'}`;
                phase = 'recover';
                requestStatus();
            })),
        ]).then(() => {
            if (disposed) return;
            registered = true;
            requestStatus();
        }).catch(err => fail(String(err)));

        const poll = setInterval(() => {
            if (phase === 'ready' && !pendingStatus) requestStatus();
        }, 2000);
        return () => {
            disposed = true;
            clearTimeout(timer);
            clearInterval(poll);
            unlisteners.forEach(unlisten => unlisten());
        };
    }, [dispatch, registrationAttempt]);

    return {mode, status, error, hasState,
        retry: () => actions.current.retry(),
        refresh: () => actions.current.refresh(),
        changeMode: (next: Mode) => actions.current.changeMode(next)};
}
