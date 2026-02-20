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

import {useState, useEffect, useRef} from 'react';
import {Settings, Wifi, Clock, Lock, Shield, Info, User} from 'lucide-react';
import {motion, AnimatePresence} from 'motion/react';

import {Switch} from "./ui/switch";

import {InlineNotification, Notification} from './ui/inline-notification';
import {PowerButton} from './ui/power-button';
import {ClientTabProps} from '../commons/Tab';
import {
    ClientConnectionInfo,
    ClientStatus,
    CommandType,
    EventType,
    ServerChoice,
    ServerFound,
    ServiceError,
    StreamType
} from '../api/Interface';
import {listenCommand, listenGeneralEvent} from '../api/Listener';
import {setOtp, startClient, stopClient, chooseServer, saveClientConfig, switchTrayIcon} from '../api/Sender';
import {useEventListeners} from '../hooks/useEventListeners';

import {parseStreams, isValidIpAddress} from '../api/Utility'
import {PermissionsPanel} from './ui/permissions-panel';
import {abbreviateText, CopyableBadge} from './ui/copyable-badge';
import {ServerSelectionPanel} from './ui/server-selection-panel';
import {OtpInputPanel} from './ui/otp-input-panel';
import {ActionButton} from './ui/action-button';

export function ClientTab({onStatusChange, state}: ClientTabProps) {
    let previousState = useRef<ClientStatus | null>(null);

    const [clientHostname, setClientHostname] = useState(state.client_hostname || '');
    const [runningPending, setRunningPending] = useState(false);
    const [isRunning, setIsRunning] = useState(state.running);
    const [isConnected, setIsConnected] = useState(state.connected);
    const [showSecurity, setShowSecurity] = useState(false);
    const [showOptions, setShowOptions] = useState(false);
    const [showOtpInput, setShowOtpInput] = useState(state.otp_needed);
    const [showServerChoice, setShowServerChoice] = useState(state.service_choice_needed);
    const [availableServers, setAvailableServers] = useState<ServerFound[] | null>(() => {
        if (state.service_choice_needed && state.available_servers) {
            return state.available_servers;
        }
        return null;
    });
    const [currentConnection, setCurrentConnection] = useState<ClientConnectionInfo | null>(state.server_info);
    // const [autoConnect, setAutoConnect] = useState(false);
    const [autoReconnect, setAutoReconnect] = useState(state.server_info.auto_reconnect);
    const [enableMouse, setEnableMouse] = useState(parseStreams(state.streams_enabled).includes(StreamType.Mouse));
    const [enableKeyboard, setEnableKeyboard] = useState(parseStreams(state.streams_enabled).includes(StreamType.Keyboard));
    const [enableClipboard, setEnableClipboard] = useState(parseStreams(state.streams_enabled).includes(StreamType.Clipboard));
    const [requireSSL, setRequireSSL] = useState(state.ssl_enabled);
    const [hostname, setHostname] = useState(state.server_info.hostname || '');
    const [host, setHost] = useState(state.server_info.host || state.server_info.hostname || '');
    const [port, setPort] = useState(state.server_info.port ? state.server_info.port.toString() : '8080');
    const [connectionTime, setConnectionTime] = useState(() => {
            if (state.start_time) {
                let startDate = new Date(state.start_time);
                let now = new Date();
                return Math.floor((now.getTime() - startDate.getTime()) / 1000);
            }
            return 0;
        }
    );
    // const [dataUsage, setDataUsage] = useState(0);
    const [controlStatus, setControlStatus] = useState<'none' | 'controlled' | 'idle'>('none');
    const [notifications, setNotifications] = useState<Notification[]>([]);
    const [pendingForceStop, setPendingForceStop] = useState(false);

    const listeners = useEventListeners('client-tab');
    const connectionListeners = handleConnectionListeners();
    const saveOptionsTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

    const handleToggleSecurity = () => {
        setShowSecurity(!showSecurity);
        setShowOptions(false);
    };

    const handleToggleOptions = () => {
        setShowOptions(!showOptions);
        setShowSecurity(false);
    };

    useEffect(() => {
        if (!isConnected) return;

        // const controlInterval = setInterval(() => {
        //   const random = Math.random();
        //   if (random > 0.9) {
        //     setControlStatus('controlled');
        //     addNotification('warning', 'Server is controlling', `${enableMouse ? 'Mouse' : ''}${enableMouse && enableKeyboard ? ' & ' : ''}${enableKeyboard ? 'Keyboard' : ''}`);
        //   } else if (random < 0.1) {
        //     setControlStatus('idle');
        //   }
        // }, 1000);

        const timeInterval = setInterval(() => {
            setConnectionTime(prev => prev + 1);
        }, 1000);

        // const dataInterval = setInterval(() => {
        //   setDataUsage(prev => prev + Math.random() * 30);
        // }, 2000);

        return () => {
            // clearInterval(controlInterval);
            clearInterval(timeInterval);
            // clearInterval(dataInterval);
        };
    }, [isConnected]);

    useEffect(() => {
        if (previousState.current === null) {
            previousState.current = state;
        } else if (JSON.stringify(previousState.current) !== JSON.stringify(state)) {
            previousState.current = state;
        } else {
            return; // No changes detected
        }
        console.log('[Client] State updated', state);
        onStatusChange(state.running);
        setIsRunning(state.running);
        switchTrayIcon(state.connected);
        setIsConnected(state.connected);

        setCurrentConnection(state.server_info);
        setClientHostname(state.client_hostname || '');

        setHost(state.server_info.host || '');
        setHostname(state.server_info.hostname || '');
        setPort(state.server_info.port ? state.server_info.port.toString() : '');
        setAutoReconnect(state.server_info.auto_reconnect);
        setRequireSSL(state.ssl_enabled);
        setShowServerChoice(state.service_choice_needed);
        setShowOtpInput(state.otp_needed);

        if (state.service_choice_needed && state.available_servers) {
            setAvailableServers(state.available_servers);
        }
        let permissions = parseStreams(state.streams_enabled);
        setEnableMouse(permissions.includes(StreamType.Mouse));
        setEnableKeyboard(permissions.includes(StreamType.Keyboard));
        setEnableClipboard(permissions.includes(StreamType.Clipboard));

        if (state.running) {
            connectionListeners.cleanup();
            connectionListeners.setup();

            if (state.start_time) {
                let startDate = new Date(state.start_time);
                let now = new Date();
                setConnectionTime(Math.floor((now.getTime() - startDate.getTime()) / 1000));
            }
        }

    }, [state]);

    function handleConnectionListeners() {

        const setup = () => {
            listenGeneralEvent(EventType.Connected, false, (event) => {
                let res = event.data as ClientConnectionInfo;
                setCurrentConnection(res);
                setHost(res.host);
                setPort(res.port.toString());
                setIsConnected(true);
                switchTrayIcon(true);
                setShowOtpInput(false);
                addNotification('success', 'Connected', `${res.host}:${res.port}`);
            }).then((unlisten) => {
                listeners.addListenerOnce('client-connected', unlisten);
            });

            listenGeneralEvent(EventType.Disconnected, false, () => {
                setIsConnected(false);
                switchTrayIcon(false);
                setConnectionTime(0);
                // setDataUsage(0);
                setControlStatus('none'); //TODO: Implement in backend
                setShowOtpInput(false);
                addNotification('warning', 'Disconnected');
            }).then((unlisten) => {
                listeners.addListenerOnce('client-disconnected', unlisten);
            });

            listenGeneralEvent(EventType.ServerChoiceNeeded, false, (event) => {
                console.log('Server choice needed event received', event);
                let res = event.data as ServerChoice;
                if (res && res.servers && res.servers.length > 0) {
                    setAvailableServers(res.servers);
                    setShowServerChoice(true);
                }

            }).then((unlisten) => {
                listeners.addListenerOnce('server-choice-needed', unlisten);
            });

            listenGeneralEvent(EventType.OtpNeeded, false, () => {
                setShowOtpInput(true);
                listeners.removeListener('otp-needed');
            }).then((unlisten) => {
                listeners.addListenerOnce('otp-needed', unlisten);
            });

            listenGeneralEvent(EventType.ServiceError, false, (event) => {
                let res = event.data as ServiceError;
                if (!res) return;
                if (res.service_name.toLowerCase() !== 'client') return;
                addNotification('error', 'Connection Error', res.error || 'An unknown error occurred during connection');
            }).then((unlisten) => {
                listeners.addListenerOnce('client-connection-error', unlisten);
            });
        }

        const cleanup = () => {
            listeners.forceRemoveListener('client-connected');
            listeners.forceRemoveListener('client-disconnected');
            listeners.forceRemoveListener('server-choice-needed');
            listeners.forceRemoveListener('otp-needed');
            listeners.forceRemoveListener('client-connection-error');
        }

        return {setup, cleanup};
    }

    const handleStopClient = () => {
        setPendingForceStop(true);
        setRunningPending(true);

        listeners.removeListener('client-start');
        listeners.removeListener('client-start-error');
        connectionListeners.cleanup();

        listenCommand(EventType.CommandSuccess, CommandType.StopClient, (event) => {
            console.log(`Client stopped successfully`, event);
            setIsRunning(false);
            switchTrayIcon(false);
            setIsConnected(false);
            setConnectionTime(0);
            // setDataUsage(0);
            setShowOtpInput(false);
            setAvailableServers(null);
            setShowServerChoice(false);
            setControlStatus('none');
            onStatusChange(false);
            addNotification('info', 'Stopped');
            setRunningPending(false);
            setPendingForceStop(false);
            listeners.removeListener('client-stop');
            listeners.removeListener('client-stop-error');
        }).then(unlisten => {
            listeners.addListenerOnce('client-stop', unlisten);
        });

        listenCommand(EventType.CommandError, CommandType.StopClient, (event) => {
            console.error(`Error stopping client: ${event.message}`);
            addNotification('error', 'Failed to Stop', event.data?.error || 'Unknown error');

            listeners.removeListener('client-stop-error');
            listeners.removeListener('client-stop');
            setPendingForceStop(false);
        }).then(unlisten => {
            listeners.addListenerOnce('client-stop-error', unlisten);
        });

        stopClient().catch((err) => {
            console.error('Error invoking stopClient:', err);
            addNotification('error', 'Failed to Stop', err.message || 'Unknown error');
            setRunningPending(false);
            setPendingForceStop(false);
            listeners.forceRemoveListener('client-stop-error');
            listeners.forceRemoveListener('client-stop');
        });
    }

    const handleToggleClient = () => {
        if (!isRunning) {
            setRunningPending(true);
            onStatusChange(true);

            listenCommand(EventType.CommandSuccess, CommandType.StartClient, (event) => {
                console.log(`Client started successfully`, event);
                setIsRunning(true);
                let res = event.data?.result as ClientConnectionInfo;
                if (res) {
                    let permissions = parseStreams(event.data?.result.enabled_streams as number[]);
                    setEnableMouse(permissions.includes(StreamType.Mouse));
                    setEnableKeyboard(permissions.includes(StreamType.Keyboard));
                    setEnableClipboard(permissions.includes(StreamType.Clipboard));
                    onStatusChange(true);
                    addNotification('success', 'Started');
                    setRunningPending(false);
                }

                listeners.removeListener('client-start');
                listeners.removeListener('client-start-error');
            }).then(unlisten => {
                listeners.addListenerOnce('client-start', unlisten);
            });

            listenCommand(EventType.CommandError, CommandType.StartClient, (event) => {
                console.error(`Error starting client: ${event.message}`);
                addNotification('error', 'Connection Failed', event.data?.error || 'Unknown error');
                setRunningPending(false);
                setIsRunning(false);
                switchTrayIcon(false);
                setIsConnected(false);
                onStatusChange(false);

                setShowOtpInput(false);
                setShowServerChoice(false);
                setAvailableServers(null);


                listeners.removeListener('client-start-error');
                listeners.removeListener('client-start');
                connectionListeners.cleanup();
            }).then(unlisten => {
                listeners.addListenerOnce('client-start-error', unlisten);
            });

            startClient().then(() => {
                connectionListeners.setup();
            }).catch((err) => {
                console.error('Error invoking startClient:', err);
                addNotification('error', 'Connection Failed', err.message || 'Unknown error');
                setRunningPending(false);
                onStatusChange(false);
                listeners.forceRemoveListener('client-start-error');
                listeners.forceRemoveListener('client-start');
            });

        } else {
            handleStopClient();
        }
    };

    const handleOtpSubmit = (otp: string) => {
        listenCommand(EventType.CommandSuccess, CommandType.SetOtp, (event) => {
            console.log(`OTP accepted`, event);
            setShowOtpInput(false);
            addNotification('success', 'OTP Accepted');

            listeners.removeListener('otp-success');
            listeners.removeListener('otp-error');
        }).then(unlisten => {
            listeners.addListenerOnce('otp-success', unlisten);
        });

        listenCommand(EventType.CommandError, CommandType.SetOtp, (event) => {
            console.error(`OTP rejected`, event);
            addNotification('error', 'OTP Rejected', event.data?.error || 'Unknown error');
            setShowOtpInput(false);
            listeners.removeListener('otp-error');
            listeners.removeListener('otp-success');
            handleStopClient(); // Stop the client since OTP failed
        }).then(unlisten => {
            listeners.addListenerOnce('otp-error', unlisten);
        });

        setOtp(otp)
            .catch((err) => {
                console.error('Error sending OTP:', err);
                addNotification('error', 'OTP Submission Failed', err.message || 'Unknown error');
            });
    };

    const handleCancelOtp = () => {
        setShowOtpInput(false);
        addNotification('info', 'Authentication Cancelled', 'OTP input was cancelled');
        handleStopClient(); // Stop the client since OTP was cancelled
    };

    const handleServerSelect = (serverUid: string) => {
        // Set up listeners for the command response
        listenCommand(EventType.CommandSuccess, CommandType.ChooseServer, () => {
            addNotification('success', 'Server Selected');
            setShowServerChoice(false);
            setAvailableServers(null);
            listeners.removeListener('server-select-success');
            listeners.removeListener('server-select-error');
        }).then(unlisten => {
            listeners.addListenerOnce('server-select-success', unlisten);
        });

        listenCommand(EventType.CommandError, CommandType.ChooseServer, (err) => {
            console.error('Server selection error:', err);
            addNotification('error', 'Server Selection Failed', err.data?.error || 'Failed to choose selected server');
            // Don't close the panel on error, allow user to try again
            listeners.removeListener('server-select-error');
            listeners.removeListener('server-select-success');
        }).then(unlisten => {
            listeners.addListenerOnce('server-select-error', unlisten);
        });

        chooseServer(serverUid)
            .catch((err) => {
                console.error('Error choosing server:', err);
                addNotification('error', 'Server Selection Failed', err.message || 'Unknown error');
            });
    };

    const handleCancelServerSelection = () => {
        setShowServerChoice(false);
        setAvailableServers(null);
        addNotification('info', 'Selection Cancelled', 'Server selection was cancelled');
        handleStopClient(); // Stop the client since server selection was cancelled
    };

    const handleSaveOptions = (hostValue: string, hostnameValue: string, portValue: string, sslEnabledValue: boolean, autoReconnectValue: boolean, save_feedback: boolean = true) => {

        if (save_feedback) {
            listenCommand(EventType.CommandSuccess, CommandType.SetClientConfig, (event) => {
                console.log(`Client config saved successfully`, event);
                addNotification('success', 'Options Saved');
                listeners.removeListener('save-client-config-success');
            }).then(unlisten => {
                listeners.addListenerOnce('save-client-config-success', unlisten);
            });
        }

        listenCommand(EventType.CommandError, CommandType.SetClientConfig, (event) => {
            console.error(`Error saving client config: ${event.message}`);
            addNotification('error', 'Save Failed', event.data?.error || 'Unknown error');
            listeners.removeListener('save-client-config-error');
        }).then(unlisten => {
            listeners.addListenerOnce('save-client-config-error', unlisten);
        });

        saveClientConfig(hostValue, hostnameValue, Number(portValue), sslEnabledValue, autoReconnectValue)
            .catch((err) => {
                console.error('Error saving client config:', err);
                addNotification('error', 'Save Failed', err.message || 'Unknown error');
            });
    };

    const scheduleOptionsSave = (hostValue: string, hostnameValue: string, portValue: string, sslEnabledValue: boolean, autoReconnectValue: boolean, save_feedback: boolean = true) => {
        // Clear existing timeout
        if (saveOptionsTimeoutRef.current) {
            clearTimeout(saveOptionsTimeoutRef.current);
        }

        // Schedule new save after inactivity
        saveOptionsTimeoutRef.current = setTimeout(() => {
            handleSaveOptions(hostValue, hostnameValue, portValue, sslEnabledValue, autoReconnectValue, save_feedback);
        }, 100);
    };

    const formatTime = (seconds: number) => {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = seconds % 60;
        return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    };

    // const formatData = (mb: number) => {
    //   if (mb >= 1024) {
    //     return `${(mb / 1024).toFixed(2)} GB`;
    //   }
    //   return `${mb.toFixed(0)} MB`;
    // };

    return (
        <div className="space-y-5">
            {/* Power Button */}
            <PowerButton
                status={
                    runningPending ? 'pending' :
                        isRunning && !isConnected ? 'connecting' :
                            isConnected ? 'running' :
                                'stopped'
                }
                onClick={handleToggleClient}
                onForceStop={handleStopClient}
                pendingForceStop={pendingForceStop}
                uid={state.uid}
                stoppedLabel="Disconnected"
                runningLabel="Connected"
                connectingLabel="Connecting"
            />

            {/* Inline Notifications */}
            <InlineNotification notifications={notifications}/>

            {/* OTP Input */}
            <OtpInputPanel
                isVisible={showOtpInput}
                onSubmit={handleOtpSubmit}
                onCancel={handleCancelOtp}
            />

            <ServerSelectionPanel
                serverChoice={availableServers}
                onServerSelect={handleServerSelect}
                isVisible={showServerChoice}
                onCancel={handleCancelServerSelection}
            />

            {/* Disconnected Status Info */}
            {/* {!isConnected && !showOtpInput && host && port && ( */}
            {!isConnected && !showOtpInput && (
                <motion.div
                    initial={{opacity: 0}}
                    animate={{opacity: 1}}
                    transition={{delay: 0.2}}
                    className="space-y-2"
                >
                    {/* Client Hostname */}
                    {clientHostname && !showOtpInput && (
                        <motion.div
                            whileHover={{scale: 1.02}}
                            className="flex items-center gap-3 p-4 rounded-lg border backdrop-blur-sm"
                            style={{
                                backgroundColor: 'var(--app-card-bg)',
                                borderColor: 'var(--app-card-border)'
                            }}
                        >
                            <div className="w-10 h-10 rounded-lg flex items-center justify-center"
                                 style={{backgroundColor: 'var(--app-bg-tertiary)'}}>
                                <User size={20} style={{color: 'var(--app-primary)'}}/>
                            </div>
                            <div className="flex-1">
                                <div className="text-sm font-bold" style={{color: 'var(--app-text-primary)'}}>
                                    {clientHostname}
                                </div>
                                <div className="text-xs" style={{color: 'var(--app-text-muted)'}}>Your Hostname</div>
                            </div>
                        </motion.div>
                    )}

                    {/* <motion.div
            whileHover={{ scale: 1.02 }}
            className="flex items-center gap-3 p-4 rounded-lg border backdrop-blur-sm"
            style={{ 
              backgroundColor: 'var(--app-card-bg)',
              borderColor: 'var(--app-card-border)'
            }}
          >
            <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ backgroundColor: 'var(--app-bg-tertiary)' }}>
              <Server size={20} style={{ color: 'var(--app-text-muted)' }} />
            </div>
            <div className="flex-1 grid grid-cols-2 gap-1">
              <div>
                <div className="text-sm font-bold" style={{ color: 'var(--app-text-primary)' }}>
                  {`${host || hostname}:${port}`}
                </div>
                <div className="text-xs" style={{ color: 'var(--app-text-muted)' }}>Server Address</div>
              </div>
              {currentConnection?.uid && (
                <div className="flex justify-center pt-1">
                  <CopyableBadge
                    key={currentConnection.uid}
                    fullText={currentConnection.uid}
                    displayText={abbreviateText(currentConnection.uid, 5, 4)}
                    label=""
                    titleText={`Click to copy Server UID: ${currentConnection.uid}`}
                  />
                </div>
              )}
            </div>
          </motion.div> */}

                    {/* <div className="flex gap-2">

            <motion.div 
              whileHover={{ scale: 1.02 }}
              className="flex-1 flex items-center gap-3 p-4 rounded-lg border backdrop-blur-sm"
              style={{ 
                backgroundColor: 'var(--app-card-bg)',
                borderColor: 'var(--app-card-border)'
              }}
            >
              <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ backgroundColor: 'var(--app-bg-tertiary)' }}>
                <Wifi size={20} style={{ color: 'var(--app-text-muted)' }} />
              </div>
              <div className="flex-1">
                <div className="text-sm font-bold" style={{ color: 'var(--app-text-primary)' }}>
                  Ready
                </div>
                <div className="text-xs" style={{ color: 'var(--app-text-muted)' }}>Status</div>
              </div>
            </motion.div>
          </div> */}
                </motion.div>
            )}

            {/* Connection Info */}
            {isConnected && (
                <motion.div
                    initial={{opacity: 0}}
                    animate={{opacity: 1}}
                    transition={{delay: 0.2}}
                    className="space-y-2"
                >
                    <div className="flex gap-1">
                        <motion.div
                            whileHover={{scale: 1.02}}
                            className="flex-1 flex flex-col gap-1 p-4 rounded-lg border backdrop-blur-sm"
                            style={{
                                backgroundColor: 'var(--app-card-bg)',
                                borderColor: 'var(--app-card-border)'
                            }}
                        >
                            <div className="flex items-center gap-3">
                                <div className="w-10 h-10 rounded-lg flex items-center justify-center"
                                     style={{backgroundColor: 'var(--app-primary)'}}>
                                    <Wifi size={20} style={{color: 'white'}}/>
                                </div>
                                <div className="flex-1">
                                    <div className="text-sm font-bold" style={{color: 'var(--app-text-primary)'}}>
                                        {currentConnection ? `${currentConnection.host}:${currentConnection.port}` : 'Unknown'}
                                    </div>
                                    {/* Server UID Badge */}
                                    {currentConnection?.uid && (
                                        <div className="flex justify-center pt-1">
                                            <CopyableBadge
                                                key={currentConnection.uid}
                                                fullText={currentConnection.uid}
                                                displayText={abbreviateText(currentConnection.uid, 5, 4)}
                                                label=""
                                                titleText={`Click to copy Server UID: ${currentConnection.uid}`}
                                            />
                                        </div>
                                    )}
                                    {/* <div className="text-xs" style={{ color: 'var(--app-text-muted)' }}>IP Address</div> */}
                                </div>
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
                                <Clock size={20} style={{color: 'white'}}/>
                            </div>
                            <div className="flex-1">
                                <div className="text-sm font-bold" style={{color: 'var(--app-text-primary)'}}>
                                    {formatTime(connectionTime)}
                                </div>
                                <div className="text-xs" style={{color: 'var(--app-text-muted)'}}>Duration</div>
                            </div>
                        </motion.div>
                    </div>

                    {/* Client Hostname - Full Width
          {clientHostname && (
            <motion.div
              whileHover={{ scale: 1.02 }}
              className="flex items-center gap-3 p-4 rounded-lg border backdrop-blur-sm"
              style={{
                backgroundColor: 'var(--app-card-bg)',
                borderColor: 'var(--app-card-border)'
              }}
            >
              <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ backgroundColor: 'var(--app-primary)' }}>
                <User size={20} style={{ color: 'white' }} />
              </div>
              <div className="flex-1">
                <div className="text-sm font-bold" style={{ color: 'var(--app-text-primary)' }}>
                  {clientHostname}
                </div>
                <div className="text-xs" style={{ color: 'var(--app-text-muted)' }}>Your Hostname</div>
              </div>
            </motion.div>
          )}*/}

                    {/* Data Usage - Full Width */}
                    {/* <motion.div
            whileHover={{ scale: 1.01 }}
            className="flex items-center gap-3 p-4 rounded-lg border backdrop-blur-sm"
            style={{ 
              backgroundColor: 'var(--app-card-bg)',
              borderColor: 'var(--app-card-border)'
            }}
          >
            <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ backgroundColor: 'var(--app-primary)' }}>
              <Wifi size={20} style={{ color: 'white' }} />
            </div>
            <div className="flex-1">
              <div className="text-xl font-bold" style={{ color: 'var(--app-text-primary)' }}>
                {formatData(dataUsage)}
              </div>
              <div className="text-xs" style={{ color: 'var(--app-text-muted)' }}>Data Usage</div>
            </div>
          </motion.div> */}

                    {/* Control Status */}
                    {controlStatus !== 'none' && (
                        <motion.div
                            initial={{opacity: 0, scale: 0.95}}
                            animate={{opacity: 1, scale: 1}}
                            className="p-4 rounded-lg border text-center"
                            style={{
                                backgroundColor: controlStatus === 'controlled' ? 'var(--app-warning)' : 'var(--app-card-bg)',
                                borderColor: controlStatus === 'controlled' ? 'var(--app-warning)' : 'var(--app-card-border)',
                                color: controlStatus === 'controlled' ? 'white' : 'var(--app-text-primary)'
                            }}
                        >
                            <p className="font-semibold">
                                {controlStatus === 'controlled' ? '⚠️ Server is controlling this device' : 'Standing by...'}
                            </p>
                        </motion.div>
                    )}
                </motion.div>
            )}

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
                disableAllStreams={isConnected}
            />

            {/* Settings Button
      <motion.button
        whileHover={{ scale: 1.02 }}
        whileTap={{ scale: 0.98 }}
        onClick={() => setShowOptions(!showOptions)}
        className="w-full p-3 rounded-lg transition-all duration-300 flex items-center justify-center gap-2 border-2"
        style={{
          backgroundColor: showOptions ? 'var(--app-primary)' : 'var(--app-bg-tertiary)',
          borderColor: 'var(--app-primary)',
          color: showOptions ? 'white' : 'var(--app-text-primary)'
        }}
      >
        <Settings size={20} />
        <span>Settings</span>
      </motion.button> */}

            {/* Action Buttons */}
            <div className="grid grid-cols-2 gap-2">
                <ActionButton onClick={handleToggleSecurity} clicked={showSecurity}>
                    <Shield size={20}/>
                    <span className="text-xs">Security</span>
                </ActionButton>

                <ActionButton onClick={handleToggleOptions} clicked={showOptions}>
                    <Settings size={20}/>
                    <span className="text-xs">Options</span>
                </ActionButton>
            </div>

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
                                        disabled={isConnected}
                                        onCheckedChange={(checked) => {
                                            if (isConnected) return;
                                            setRequireSSL(checked);
                                            handleSaveOptions(host, hostname, port, checked, autoReconnect, false);
                                        }}
                                    />
                                </div>
                            </div>
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
                                Client Settings
                            </h3>

                            <AnimatePresence mode="wait">
                                {((host === '' && hostname === '') || port === '') && (
                                    <motion.div
                                        initial={{opacity: 0, height: 0, scale: 0.95}}
                                        animate={{opacity: 1, height: 'auto', scale: 1}}
                                        exit={{opacity: 0, height: 0, scale: 0.95}}
                                        transition={{duration: 0.2, ease: "easeOut"}}
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
                                            <strong style={{color: 'var(--app-text-primary)'}}>Auto-discovery
                                                enabled:</strong> Host and port are optional. Leave them empty to
                                            automatically connect to available servers on your network.
                                        </p>
                                    </motion.div>
                                )}
                            </AnimatePresence>

                            <div>
                                <label className="block mb-2 font-semibold"
                                       style={{color: 'var(--app-text-primary)'}}
                                >
                                    Host
                                    <span className="ml-2 text-xs font-normal"
                                          style={{color: 'var(--app-text-muted)'}}>(optional)</span>
                                </label>
                                <input
                                    type="text"
                                    placeholder="Auto-detect"
                                    value={host || hostname}
                                    onChange={(e) => {
                                        const newHost = e.target.value;
                                        if (newHost === '') {
                                            console.log('Clearing host and hostname');
                                            setHost(newHost);
                                            setHostname(newHost);
                                        }

                                        let is_ip = isValidIpAddress(newHost);
                                        if (!is_ip && newHost !== hostname) {
                                            setHostname(newHost);
                                            setHost('');
                                            scheduleOptionsSave('', newHost, port, requireSSL, autoReconnect, false);
                                        } else if (newHost !== host) {
                                            setHostname('');
                                            setHost(newHost);
                                            scheduleOptionsSave(newHost, '', port, requireSSL, autoReconnect, false);
                                        }
                                    }}
                                    className="app-input"
                                    disabled={isRunning}
                                />
                            </div>

                            <div>
                                <label className="block mb-2 font-semibold"
                                       style={{color: 'var(--app-text-primary)'}}
                                >
                                    Port
                                    <span className="ml-2 text-xs font-normal"
                                          style={{color: 'var(--app-text-muted)'}}>(optional)</span>
                                </label>
                                <input
                                    type="text"
                                    placeholder="Auto-detect"
                                    value={port}
                                    onChange={(e) => {
                                        const newPort = e.target.value;
                                        if (newPort !== '' && !/^\d*$/.test(newPort)) {
                                            return; // Only allow numeric input
                                        }
                                        setPort(newPort);
                                        scheduleOptionsSave(host, hostname, newPort, requireSSL, autoReconnect, false);
                                    }}
                                    className="app-input"
                                    disabled={isRunning}
                                />
                            </div>

                            {/* <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <label className="flex items-center gap-2"
                    style={{ color: 'var(--app-text-primary)' }}
                    title="Automatically start the client when the application launches"
                  >
                    <span>Start automatically</span>
                  </label>
                  <Switch
                    id="autoConnect"
                    checked={autoConnect}
                    onCheckedChange={(checked) => {
                      setAutoConnect(checked);
                    }}
                    disabled={true} // Feature not implemented yet
                  />
                </div>
              </div> */}

                            <div className="space-y-3">
                                <div className="flex items-center justify-between">
                                    <label className="flex items-center gap-2"
                                           style={{color: 'var(--app-text-primary)'}}
                                           title="Automatically attempt to reconnect if the connection is lost"
                                    >
                                        <span>Auto-reconnect</span>
                                    </label>
                                    <Switch
                                        id="autoReconnect"
                                        checked={autoReconnect}
                                        onCheckedChange={(checked) => {
                                            setAutoReconnect(checked);
                                            handleSaveOptions(host, hostname, port, requireSSL, checked, false);
                                        }}
                                    />
                                </div>
                            </div>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}