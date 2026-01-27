import { useState, useEffect, useRef} from 'react';

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "./ui/select";

import { Switch } from  "./ui/switch";

import { ScrollArea } from './ui/scrollbar';

import { Settings, Users, Activity, Plus, Trash2, Key, Lock, Shield } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { InlineNotification, Notification } from './ui/inline-notification';
import { PowerButton } from './ui/power-button';
import { PermissionsPanel } from './ui/permissions-panel';

import { useEventListeners } from '../hooks/useEventListeners';
import { useClientManagement } from '../hooks/useClientManagement';
import { 
  shareCertificate, 
  startServer, stopServer, 
  saveServerConfig, 
  addClient as addClientCommand, removeClient as removeClientCommand} from '../api/Sender';
import { listenCommand, listenGeneralEvent } from '../api/Listener';
import { EventType, CommandType, ClientObj, StreamType, ServerStatus, OtpInfo, ClientEditObj} from '../api/Interface';

import { ServerTabProps } from '../commons/Tab'
import { parseStreams, isValidIpAddress } from '../api/Utility'
import { abbreviateText, CopyableBadge } from './ui/copyable-badge';
import { SelectPortal, SelectViewport } from '@radix-ui/react-select';

export function ServerTab({ onStatusChange, state }: ServerTabProps) {
  let previousState = useRef<ServerStatus | null>(null);

  const [runningPending, setRunningPending] = useState(false);
  const [isRunning, setIsRunning] = useState(state.running);
  const [showOptions, setShowOptions] = useState(false);
  const [showClients, setShowClients] = useState(false);
  const [showSecurity, setShowSecurity] = useState(false);
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
  const [firstInit, setFirstInit] = useState(true);
  const [newClientIp, setNewClientIp] = useState('');
  const [newClientPosition, setNewClientPosition] = useState<'top' | 'bottom' | 'left' | 'right'>('top');
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

  const otpFocus = useRef<HTMLDivElement>(null);
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
      addNotification(
        connected ? 'success' : 'warning', 
        connected ? 'Client Connected' : 'Client Disconnected', 
        `${clientData.host_name ? clientData.host_name : clientData.ip_address} (${clientData.screen_position.toUpperCase()})`
      );
    }
    clientManager.updateClientStatus(clientData, connected);
  };

  function handleClientEventListeners() {

      const setup = () => {
        listenGeneralEvent(EventType.ClientConnected,false, (event) => {
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
      };

      const cleanup = () => {
        listeners.removeListener('client-connected');
        listeners.removeListener('client-disconnected');
      };

      return {cleanup, setup};
  };

  const handleToggleServer = () => {
    if (!isRunning) {
      setRunningPending(true);
      onStatusChange(true);

      listenCommand(EventType.CommandSuccess, CommandType.StartServer, (event) => {
        console.log(`Server started successfully: ${event.message}`);
        setIsRunning(true);
        let res = event.data?.result;
        if (res) {
          addNotification('success', 'Server started', `Listening on ${res.host}:${res.port}`);
          setPort(res.port.toString());
          setRunningPending(false);

          let start_time = res.start_time;
          // Parse timestamp isoformat
          if (start_time) {
            let startDate = new Date(start_time);
            let now = new Date();
            let uptimeSeconds = Math.floor((now.getTime() - startDate.getTime()) / 1000);
            setUptime(uptimeSeconds);
          }
        }
        
        listeners.removeListener('start-server');
        listeners.removeListener('start-server-error');
      }).then(unlisten => {
        listeners.addListener('start-server', unlisten);
      });

      listenCommand(EventType.CommandError, CommandType.StartServer, (event) => {
        addNotification('error', 'Failed', event.data?.error || '');
        setRunningPending(false);
        onStatusChange(false);

        listeners.removeListener('start-server');
        listeners.removeListener('start-server-error');
      }).then(unlisten => {
        listeners.addListener('start-server-error', unlisten);
      });

      startServer().then(() => {
        clientEventHandler.setup();
      }).catch((err) => {
        console.error('Error starting server:', err);
        addNotification('error', 'Failed to start server');
        setRunningPending(false);
        onStatusChange(false);

        // Cleanup
        listeners.removeListener('start-server');
        listeners.removeListener('start-server-error');
      });
      
    } else {
      setRunningPending(true);

      clientEventHandler.cleanup();

      // Setup one-time listener
      listenCommand(EventType.CommandSuccess, CommandType.StopServer, (event) => {
        console.log(`Server stopped successfully: ${event.message}`);
        setIsRunning(false);
        clientManager.disconnectAll();
        setUptime(0);
        setOtp('');
        addNotification('warning', 'Server stopped');
        onStatusChange(false);
        setRunningPending(false);
        
        // Auto-unlisten
        listeners.removeListener('stop-server');
      }).then(unlisten => {
        listeners.addListener('stop-server', unlisten);
      });

      stopServer().catch((err) => {
        console.error('Error stopping server:', err);
        addNotification('error', 'Failed to stop server');
        setRunningPending(false);
        // Cleanup listener
        listeners.removeListener('stop-server');
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
        addNotification('success', 'OTP Generated', `Code: ${result.otp}`);
        if (result.timeout && result.timeout > 0) {
          setTimeout(() => {
            setOtp('');
            addNotification('info', 'OTP Expired');
          }, result.timeout * 1000);
        }

        setTimeout(() => {
          otpFocus.current?.scrollIntoView({ behavior: 'smooth' }); 
        }, 5);
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
      setOtp('');
      setOtpRequested(false);
    });
    setOtpRequested(true);
  };

  const addClient = () => {
    if (isAddingClient) {
      return;
    }

    if (firstInit) {
      addNotification('info', "Choose a position for the new client");
      return;
    }

    if (!newClientIp || !newClientPosition) {
      addNotification('error', 'Missing information');
      return;
    }

    let ip = isValidIpAddress(newClientIp) ? newClientIp : '';
    let hostname = ip === '' ? newClientIp : '';

    // Check if a client with the same IP or hostname already exists
    let existing = clientManager.clients.find(c => ip !== '' ? c.ip === ip : hostname !== '' ? c.name === hostname : null);
    if (existing) {
      addNotification('error', 'Client already exists');
      return;
    }

    setIsAddingClient(true)

    listenCommand(EventType.CommandSuccess, CommandType.AddClient, (event) => {
      console.log(`Client added successfully: ${event.message}`);
      let result = event.data?.result as ClientEditObj;
      if (result) {
        addNotification('info', 'Client added', `${hostname || ip} (${newClientPosition.toUpperCase()})`);
        setNewClientIp('');
        setNewClientPosition('top');
        setFirstInit(true);

        clientManager.addClient(hostname, ip, newClientPosition);

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
      setFirstInit(true);
      listeners.removeListener('add-client-error');
      listeners.removeListener('add-client');
    }).then(unlisten => {
        listeners.addListenerOnce('add-client-error', unlisten);
    });
    
    addClientCommand(hostname, ip, newClientPosition).catch((err) => {
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
      addNotification('info', `${client?.name || client?.ip} removed`);
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

    removeClientCommand(client?.name || '', client?.ip || '').catch((err) => {
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
    console.log('Saving options:', { host: hostValue, port: portValue, sslEnabled: sslEnabledValue});
    
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
    
    // Schedule new save after 2 seconds of inactivity
    saveOptionsTimeoutRef.current = setTimeout(() => {
      handleSaveOptions(hostValue, portValue, sslEnabledValue);
    }, 2000);
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

      {/* Inline Notifications */}
      <InlineNotification notifications={notifications} />

      {/* Server Information - Horizontal Layout */}
      <motion.div 
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.2 }}
        className="flex gap-2"
      >
        <motion.div 
          whileHover={{ scale: 1.02 }}
          className="flex-1 flex items-center gap-3 p-4 rounded-lg border backdrop-blur-sm"
          style={{ 
            backgroundColor: 'var(--app-card-bg)',
            borderColor: 'var(--app-card-border)'
          }}
        >
          <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ backgroundColor: 'var(--app-primary)' }}>
            <Users size={20} style={{ color: 'white' }} />
          </div>
          <div className="flex-1">
            <motion.div 
              key={clientManager.connectedCount}
              initial={{ scale: 1.3 }}
              animate={{ scale: 1 }}
              className="text-xl font-bold"
              style={{ color: 'var(--app-text-primary)' }}
            >
              {clientManager.connectedCount}
            </motion.div>
            <div className="text-xs" style={{ color: 'var(--app-text-muted)' }}>Connected</div>
          </div>
        </motion.div>

        <motion.div 
          whileHover={{ scale: 1.02 }}
          className="flex-1 flex items-center gap-3 p-4 rounded-lg border backdrop-blur-sm"
          style={{ 
            backgroundColor: 'var(--app-card-bg)',
            borderColor: 'var(--app-card-border)'
          }}
        >
          <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ backgroundColor: 'var(--app-primary)' }}>
            <Activity size={20} style={{ color: 'white' }} />
          </div>
          <div className="flex-1">
            <div className="text-xl font-bold" style={{ color: 'var(--app-text-primary)' }}>
              {formatUptime(uptime)}
            </div>
            <div className="text-xs" style={{ color: 'var(--app-text-muted)' }}>Uptime</div>
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
      <div className="grid grid-cols-3 gap-2">
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={handleToggleClients}
          className="cursor-pointer p-3 rounded-lg transition-all duration-300 flex flex-col items-center gap-1 border-2"
          style={{
            backgroundColor: showClients ? 'var(--app-primary)' : 'var(--app-bg-tertiary)',
            borderColor: 'var(--app-primary)',
            color: showClients ? 'white' : 'var(--app-text-primary)'
          }}
        >
          <Users size={20} />
          <span className="text-xs">Clients</span>
        </motion.button>

        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={handleToggleSecurity}
          className="cursor-pointer p-3 rounded-lg transition-all duration-300 flex flex-col items-center gap-1 border-2"
          style={{
            backgroundColor: showSecurity ? 'var(--app-primary)' : 'var(--app-bg-tertiary)',
            borderColor: 'var(--app-primary)',
            color: showSecurity ? 'white' : 'var(--app-text-primary)'
          }}
        >
          <Shield size={20} />
          <span className="text-xs">Security</span>
        </motion.button>

        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={handleToggleOptions}
          className="cursor-pointer p-3 rounded-lg transition-all duration-300 flex flex-col items-center gap-1 border-2"
          style={{
            backgroundColor: showOptions ? 'var(--app-primary)' : 'var(--app-bg-tertiary)',
            borderColor: 'var(--app-primary)',
            color: showOptions ? 'white' : 'var(--app-text-primary)'
          }}
        >
          <Settings size={20} />
          <span className="text-xs">Options</span>
        </motion.button>
      </div>

      {/* Clients Section */}
      <AnimatePresence>
        {showClients && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
            className="overflow-hidden"
          >
            <div className="space-y-4 p-4 rounded-lg border overflow-visible"
              style={{ 
                backgroundColor: 'var(--app-card-bg)',
                borderColor: 'var(--app-card-border)'
              }}
            >
              <h3 className="font-semibold flex items-center gap-2"
                style={{ color: 'var(--app-text-primary)' }}
              >
                <Users size={18} />
                Manage Clients
              </h3>
              
              <div className="space-y-2">
                {/* <input
                  type="text"
                  placeholder="Client name"
                  value={newClientName}
                  onChange={(e) => setNewClientName(e.target.value)}
                  className="w-full p-3 rounded-lg focus:outline-none transition-colors"
                  style={{
                    backgroundColor: 'var(--app-input-bg)',
                    border: '2px solid var(--app-input-border)',
                    color: 'var(--app-text-primary)'
                  }}
                  onFocus={(e) => e.currentTarget.style.borderColor = 'var(--app-primary)'}
                  onBlur={(e) => e.currentTarget.style.borderColor = 'var(--app-input-border)'}
                /> */}
                <input
                  type="text"
                  placeholder="IP Address or Hostname"
                  value={newClientIp}
                  onChange={(e) => setNewClientIp(e.target.value)}
                  className="app-input"
                />
                {/* Position Selector */}
                <Select value={firstInit ? '' : newClientPosition} onValueChange={(v) => {
                  if (firstInit) setFirstInit(false);
                  setNewClientPosition(v as 'top' | 'bottom' | 'left' | 'right');
                }}>
                  <SelectTrigger className="SelectTrigger">
                    <SelectValue placeholder="Select Position" />
                  </SelectTrigger>
                  <SelectPortal>
                    <SelectContent className="SelectContent">
                      <SelectViewport className="SelectViewport">
                        <SelectGroup>
                          <SelectLabel className="SelectLabel"> Screen Position</SelectLabel>
                          <SelectItem className="SelectItem" disabled={clientManager.clients.some(c => c.position === 'top')} value="top">Top</SelectItem>
                          <SelectItem className="SelectItem" disabled={clientManager.clients.some(c => c.position === 'bottom')} value="bottom">Bottom</SelectItem>
                          <SelectItem className="SelectItem" disabled={clientManager.clients.some(c => c.position === 'left')} value="left">Left</SelectItem>
                          <SelectItem className="SelectItem" disabled={clientManager.clients.some(c => c.position === 'right')} value="right">Right</SelectItem>
                        </SelectGroup>
                      </SelectViewport>
                    </SelectContent>
                  </SelectPortal>

                </Select>

                <motion.button
                  whileHover={{ scale: 1.02 }}
                  whileTap={{ scale: 0.98 }}
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
                  <Plus size={20} />
                  Add Client
                </motion.button>
              </div>

              <ScrollArea extraPadding='pl-2.5' className="space-y-2 max-h-60 overflow-y-auto custom-scrollbar w-full">
                {clientManager.clients.map(client => (
                  <motion.div
                    key={client.id}
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: 20 }}
                    className="p-3 rounded-lg border grid grid-cols-[1fr_auto_auto] gap-3 items-center"
                    style={{
                      backgroundColor: client.status === 'online' ? 'var(--app-success-bg)' : 'var(--app-input-bg)',
                      borderColor: client.status === 'online' ? 'var(--app-success)' : 'var(--app-input-border)'
                    }}
                  >
                    {/* Client Info */}
                    <div className="min-w-0">
                      <p className="font-semibold truncate cursor-help"
                        style={{ color: 'var(--app-text-primary)' }}
                        title={client.name}
                      >{client.name}</p>
                      <p className="text-sm truncate cursor-help"
                        style={{ color: 'var(--app-text-muted)' }}
                        title={client.ip}
                      >{client.ip}</p>
                    </div>

                    {/* Badges Section */}
                    <div className="flex flex-col items-end gap-2">
                      <div className="flex items-center gap-2">
                        <span className="px-2 py-1 text-xs rounded min-w-[64px] text-center"
                          style={{ 
                            backgroundColor: client.status === 'offline' ? 'var(--app-bg-tertiary)' : 'var(--app-input-bg)',
                            color: 'var(--app-primary-light)' 
                          }}
                        >
                          {client.position.charAt(0).toUpperCase() + client.position.slice(1)}
                        </span>
                        <span className="px-2 py-1 rounded text-xs min-w-[56px] text-center"
                          style={{
                            backgroundColor: client.status === 'online' ? 'var(--app-input-bg)' : 'var(--app-bg-tertiary)',
                            color: client.status === 'online' ? 'var(--app-success)' : 'var(--app-text-muted)'
                          }}
                        >
                          {client.status.charAt(0).toUpperCase() + client.status.slice(1)}
                        </span>
                      </div>
                      { client.uid && (
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
                      ) }
                    </div>

                    {/* Delete Button */}
                    <motion.button
                      whileHover={{ scale: 1.1 }}
                      whileTap={{ scale: 0.9 }}
                      onClick={() => removeClient(client.id)}
                      className="cursor-pointer p-2 transition-colors"
                      style={{ color: 'var(--app-danger)' }}
                    >
                      <Trash2 size={16} />
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
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
            className="overflow-hidden"
          >
            <div className="space-y-4 p-4 rounded-lg border"
              style={{ 
                backgroundColor: 'var(--app-card-bg)',
                borderColor: 'var(--app-card-border)'
              }}
            >
              <h3 className="font-semibold flex items-center gap-2"
                style={{ color: 'var(--app-text-primary)' }}
              >
                <Shield size={18} />
                Security Settings
              </h3>

              <div className="space-y-3">                   
                <div className="flex items-center justify-between">
                  <label className="flex items-center gap-2"
                    style={{ color: 'var(--app-text-primary)' }}
                  >
                    <Lock size={18} />
                    <span>Require SSL</span>
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
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="pt-4 space-y-3"
                  style={{ borderTop: '1px solid var(--app-border)' }}
                >
                  <div className="flex items-center justify-between">
                    <span style={{ color: 'var(--app-text-primary)' }}>One-Time Password</span>
                    <motion.button
                      whileHover={otp !== '' || otpRequested ? { scale: 1.05 } : undefined}
                      whileTap={{ scale: 0.95 }}
                      disabled={otp !== '' || otpRequested}
                      onClick={generateOtp}
                      className="px-4 py-2 rounded-lg transition-all flex items-center gap-2 cursor-pointer"
                      style={{
                        backgroundColor: otp !== '' || otpRequested ? 'var(--app-bg-tertiary)' : 'var(--app-primary)',
                        color: 'white'
                      }}
                    >
                      <Key size={16} />
                      Generate
                    </motion.button>
                  </div>
                  {otp && (
                    <motion.div
                      ref={otpFocus}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className="text-center p-4 rounded-lg border"
                      style={{
                        backgroundColor: 'var(--app-input-bg)',
                        borderColor: 'var(--app-primary)'
                      }}
                    >
                      <p className="text-3xl font-bold tracking-wider"
                        style={{ color: 'var(--app-primary-light)' }}
                      >{otp}</p>
                      <p className="text-sm mt-2"
                        style={{ color: 'var(--app-text-muted)' }}
                      >Expires in {otpTimeout}s</p>
                    </motion.div>
                  )}
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
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
            className="overflow-hidden"
          >
            <div className="space-y-4 p-4 rounded-lg border"
              style={{ 
                backgroundColor: 'var(--app-card-bg)',
                borderColor: 'var(--app-card-border)'
              }}
            >
              <h3 className="font-semibold flex items-center gap-2"
                style={{ color: 'var(--app-text-primary)' }}
              >
                <Settings size={18} />
                Server Options
              </h3>

              <div>
                <label className="block mb-2 font-semibold"
                  style={{ color: 'var(--app-text-primary)' }}
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
                  style={{ color: 'var(--app-text-primary)' }}
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
                  style={{ color: 'var(--app-text-primary)' }}
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