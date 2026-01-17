import { useState, useEffect, useRef} from 'react';
import { Power, Settings, Users, Activity, Plus, Trash2, Key, Lock, MousePointer, Keyboard, Shield, Clipboard } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { InlineNotification, Notification } from './inline-notification';
import { ClientInfoPopup } from './client-info-popup';
import { CopyableBadge, abbreviateText } from './copyable-badge';

import { useEventListeners } from '../hooks/useEventListeners';
import { useClientManagement } from '../hooks/useClientManagement';
import { 
  shareCertificate, 
  startServer, stopServer, 
  saveServerConfig, 
  addClient as addClientCommand, removeClient as removeClientCommand, 
  enableStream, disableStream} from '../api/Sender';
import { listenCommand, listenGeneralEvent } from '../api/Listener';
import { EventType, CommandType, ClientObj, StreamType, ServerStatus, OtpInfo, ClientEditObj} from '../api/Interface';

import { ServerTabProps } from '../commons/Tab'
import { parseStreams, isValidIpAddress } from '../api/Utility'

export function ServerTab({ onStatusChange, state }: ServerTabProps) {
  let previousState: ServerStatus | null = null;

  const [runningPending, setRunningPending] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [showOptions, setShowOptions] = useState(false);
  const [showClients, setShowClients] = useState(false);
  const [showSecurity, setShowSecurity] = useState(false);
  const [uid, setUid] = useState(state.uid);
  const [port, setPort] = useState(state.port.toString());
  const [host, setHost] = useState(state.host);
  const [enableMouse, setEnableMouse] = useState(false);
  const [enableKeyboard, setEnableKeyboard] = useState(false);
  const [enableClipboard, setEnableClipboard] = useState(false);
  const [requireSSL, setRequireSSL] = useState(state.ssl_enabled);
  const [otp, setOtp] = useState('');
  const [otpRequested, setOtpRequested] = useState(false);
  const [otpTimeout, setOtpTimeout] = useState(30);
  const [newClientIp, setNewClientIp] = useState('');
  const [newClientPosition, setNewClientPosition] = useState<'top' | 'bottom' | 'left' | 'right'>('top');
  const [uptime, setUptime] = useState(0);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [hoveredClientId, setHoveredClientId] = useState<string | null>(null);
  const [showPopup, setShowPopup] = useState(false);
  const [clientRect, setClientRect] = useState<DOMRect | null>(null);

  const clientManager = useClientManagement();
  const listeners = useEventListeners('server-tab');
  const clientEventHandler = handleClientEventListeners();

  const otpFocus = useRef<HTMLDivElement>(null);
  const hoverTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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
    if (previousState === null) {
      previousState = state;
    } else if (JSON.stringify(previousState) !== JSON.stringify(state)) {
      previousState = state;
    } else {
      return; // No changes detected
    }
    console.log('Server state updated:', state);
    setIsRunning(state.running);
    onStatusChange(state.running);
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

    if (state.running) { handleClientEventListeners(); }

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
        listenGeneralEvent(EventType.ClientConnected, (event) => {
          // Handle client connected event here
          let client_data = event.data as ClientObj;
          handleClientConnected(client_data, true, true);
        }).then(unlisten => {
          listeners.addListenerOnce('client-connected', unlisten);
        });

        listenGeneralEvent(EventType.ClientDisconnected, (event) => {
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

      listenCommand(EventType.CommandSuccess, CommandType.StartServer, (event) => {
        console.log(`Server started successfully: ${event.message}`);
        setIsRunning(true);
        let res = event.data?.result;
        if (res) {
          addNotification('success', 'Server started', `Listening on ${res.host}:${res.port}`);
          onStatusChange(true);
          setPort(res.port.toString());
          setRunningPending(false);
        }
        
        listeners.removeListener('start-server');
        listeners.removeListener('start-server-error');
      }).then(unlisten => {
        listeners.addListener('start-server', unlisten);
      });

      listenCommand(EventType.CommandError, CommandType.StartServer, (event) => {
        addNotification('error', 'Failed', event.data?.error || '');
        setRunningPending(false);

        listeners.removeListener('start-server');
        listeners.removeListener('start-server-error');
      }).then(unlisten => {
        listeners.addListener('start-server-error', unlisten);
      });

      clientEventHandler.setup();

      startServer().catch((err) => {
        console.error('Error starting server:', err);
        addNotification('error', 'Failed to start server');
        setRunningPending(false);

        // Cleanup
        listeners.removeListener('start-server');
        listeners.removeListener('start-server-error');
        clientEventHandler.cleanup(); // Remove client event listeners
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

    listenCommand(EventType.CommandSuccess, CommandType.AddClient, (event) => {
      console.log(`Client added successfully: ${event.message}`);
      let result = event.data?.result as ClientEditObj;
      if (result) {
        addNotification('info', 'Client added', `${hostname || ip} (${newClientPosition.toUpperCase()})`);
        setNewClientIp('');
        setNewClientPosition('top');

        clientManager.addClient(hostname, ip, newClientPosition);

        listeners.removeListener('add-client');
        listeners.removeListener('add-client-error');
      }
    }).then(unlisten => {
        listeners.addListener('add-client', unlisten);
    });

    listenCommand(EventType.CommandError, CommandType.AddClient, (event) => {
      addNotification('error', 'Failed to add client', event.data?.error || '');
      listeners.removeListener('add-client-error');
      listeners.removeListener('add-client');
    }).then(unlisten => {
        listeners.addListener('add-client-error', unlisten);
    });
    
    addClientCommand(hostname, ip, newClientPosition).catch((err) => {
      console.error('Error adding client:', err);
      addNotification('error', err.toString());
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

  const handleStreamToggle = (streamType: StreamType, enable: boolean, setState: React.Dispatch<React.SetStateAction<boolean>>) => {
    if (enable) {
      listenCommand(EventType.CommandSuccess, CommandType.EnableStream, (event) => {
        console.log(`Stream enabled successfully: ${event.message}`);
        setState(true);
        listeners.removeListener('enable-stream-' + StreamType[streamType]);
      }).then(unlisten => {
          listeners.addListener('enable-stream-' + StreamType[streamType], unlisten);
      });

      listenCommand(EventType.CommandError, CommandType.EnableStream, (event) => {
        addNotification('error', `Failed to enable ${StreamType[streamType]} stream`, event.data?.error || '');
        setState(false);
        listeners.removeListener('enable-stream-error-' + StreamType[streamType]);
      }).then(unlisten => {
          listeners.addListener('enable-stream-error-' + StreamType[streamType], unlisten);
      });

      enableStream(streamType).catch((err) => {
        console.error(`Error enabling ${StreamType[streamType]} stream:`, err);
        addNotification('error', `Failed to enable ${StreamType[streamType]} stream`);
        listeners.forceRemoveListener('enable-stream-' + StreamType[streamType]);
        listeners.forceRemoveListener('enable-stream-error-' + StreamType[streamType]);
      });

    } else {
      listenCommand(EventType.CommandSuccess, CommandType.DisableStream, (event) => {
        console.log(`Stream disabled successfully: ${event.message}`);
        setState(false);
        listeners.removeListener('disable-stream-' + StreamType[streamType]);
      }).then(unlisten => {
          listeners.addListener('disable-stream-' + StreamType[streamType], unlisten);
      });

      listenCommand(EventType.CommandError, CommandType.DisableStream, (event) => {
        addNotification('error', `Failed to disable ${StreamType[streamType]} stream`, event.data?.error || '');
        setState(true);
        listeners.removeListener('disable-stream-error-' + StreamType[streamType]);
      }).then(unlisten => {
          listeners.addListener('disable-stream-error-' + StreamType[streamType], unlisten);
      });

      disableStream(streamType).catch((err) => {
        console.error(`Error disabling ${StreamType[streamType]} stream:`, err);
        addNotification('error', `Failed to disable ${StreamType[streamType]} stream`);
        listeners.forceRemoveListener('disable-stream-' + StreamType[streamType]);
        listeners.forceRemoveListener('disable-stream-error-' + StreamType[streamType]);
      });
    }
  }

  const formatUptime = (seconds: number) => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  };

  const handleSaveOptions = (hostValue: string, portValue: string, sslEnabledValue: boolean) => {
    console.log('Saving options:', { host: hostValue, port: portValue, sslEnabled: sslEnabledValue});
    
    listenCommand(EventType.CommandSuccess, CommandType.SetServerConfig, (event) => {
      console.log(`Server config saved successfully: ${event.message}`);
      addNotification('success', 'Options saved');
      listeners.removeListener('set-server-config');
    }).then(unlisten => {
        listeners.addListenerOnce('set-server-config', unlisten);
    });
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

  // const handleClientMouseEnter = (clientId: string, event: React.MouseEvent<HTMLDivElement>) => {
  //   // Don't show popup if hovering over interactive elements
  //   const target = event.target as HTMLElement;
  //   if (target.closest('button, a, input, select, textarea')) {
  //     return;
  //   }

  //   // Delete any existing close timeout
  //   if (closeTimeoutRef.current) {
  //     clearTimeout(closeTimeoutRef.current);
  //     closeTimeoutRef.current = null;
  //   }

  //   setHoveredClientId(clientId);
  //   const rect = event.currentTarget.getBoundingClientRect();
  //   setClientRect(rect);
  //   // Show popup after 500ms
  //   hoverTimeoutRef.current = setTimeout(() => {
  //     setShowPopup(true);
  //   }, 500);
  // };

  // const handleClientMouseLeave = () => {
  //   // Clear the timeout if the user leaves before 500ms
  //   if (hoverTimeoutRef.current) {
  //     clearTimeout(hoverTimeoutRef.current);
  //     hoverTimeoutRef.current = null;
  //   }
    
  //   // Give the user time to enter the popup before closing it
  //   closeTimeoutRef.current = setTimeout(() => {
  //     setShowPopup(false);
  //     setHoveredClientId(null);
  //     setClientRect(null);
  //   }, 200); // 200ms grace period to move the cursor into the popup
  // };

  const handlePopupMouseEnter = () => {
    // Clear the close timeout when the mouse enters the popup
    if (closeTimeoutRef.current) {
      clearTimeout(closeTimeoutRef.current);
      closeTimeoutRef.current = null;
    }
    // Clear the hover timeout if present
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
      hoverTimeoutRef.current = null;
    }
  };

  const handlePopupMouseLeave = () => {
    // Close the popup when the mouse leaves the popup
    setShowPopup(false);
    setHoveredClientId(null);
    setClientRect(null);
  };

  return (
    <div className="space-y-5">
      {/* Client Info Popup - Rendered at top level */}
      <ClientInfoPopup 
        uid={clientManager.clients.find(c => c.id === hoveredClientId)?.uid}
        show={showPopup && hoveredClientId !== null}
        clientRect={clientRect || undefined}
        onMouseEnter={handlePopupMouseEnter}
        onMouseLeave={handlePopupMouseLeave}
      />

      {/* Power Button */}
      <div className="flex flex-col items-center">
        <motion.button
          whileHover={!runningPending ? { scale: 1.05 } : {}}
          whileTap={!runningPending ? { scale: 0.95 } : {}}
          onClick={handleToggleServer}
          disabled={runningPending}
          className="w-24 h-24 rounded-full flex items-center justify-center transition-all duration-300 shadow-lg relative overflow-hidden"
          style={{
            backgroundColor: isRunning ? 'var(--app-success)' : 'var(--app-bg-tertiary)',
            color: 'white',
            opacity: runningPending ? 0.7 : 1,
            cursor: runningPending ? 'not-allowed' : 'pointer'
          }}
        >
          {runningPending && (
            <>
              {/* Spinner Animation */}
              <motion.div
                className="absolute inset-0 z-10"
                style={{
                  background: 'conic-gradient(from 0deg, transparent, rgba(255,255,255,0.4), transparent)',
                }}
                animate={{ rotate: 360 }}
                transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
              />
              {/* Pulse Effect */}
              <motion.div
                className="absolute inset-0 rounded-full"
                style={{
                  backgroundColor: 'rgba(255, 255, 255, 0.2)',
                }}
                animate={{ scale: [1, 1.2, 1], opacity: [0.5, 0, 0.5] }}
                transition={{ duration: 1.5, repeat: Infinity, ease: 'easeInOut' }}
              />
            </>
          )}
          {isRunning && !runningPending && (
            <motion.div
              className="absolute inset-0 opacity-30"
              style={{ backgroundColor: 'var(--app-success)' }}
              animate={{ scale: [1, 1.5, 1] }}
              transition={{ duration: 2, repeat: Infinity }}
            />
          )}
          <Power 
            size={48} 
            className="relative z-10" 
            style={{ opacity: runningPending ? 0.5 : 1 }}
          />
        </motion.button>
        <motion.p 
          key={runningPending ? 'pending' : (isRunning ? 'running' : 'stopped')}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-3 font-semibold"
          style={{ color: 'var(--app-text-primary)' }}
        >
          {runningPending ? '' : (isRunning ? 'Server Running' : 'Server Stopped')}
        </motion.p>
        {/* Server UID - Compact and clickable */}
        {isRunning && uid && (
          <CopyableBadge
            key={uid}
            fullText={uid}
            displayText={abbreviateText(uid)}
            label="UID"
            className="mt-2"
          />
        )}
      </div>

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
      <motion.div 
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.3 }}
        className="p-4 rounded-lg border backdrop-blur-sm"
        style={{ 
          backgroundColor: 'var(--app-card-bg)',
          borderColor: 'var(--app-card-border)'
        }}
      >
        <h4 className="text-sm font-semibold mb-2" style={{ color: 'var(--app-text-primary)' }}>
          Active Permissions
        </h4>
        <div className="flex gap-3">
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={() => {
              handleStreamToggle(StreamType.Mouse, !enableMouse, setEnableMouse);
            }}
            className="flex items-center gap-2 px-3 py-2 rounded-lg transition-all cursor-pointer"
            style={{ 
              backgroundColor: enableMouse ? 'var(--app-success-bg)' : 'var(--app-danger-bg)',
              color: enableMouse ? 'var(--app-success)' : 'var(--app-danger)'
            }}
          >
            <MousePointer size={16} />
            <span className="text-xs font-semibold">Mouse</span>
          </motion.button>

          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={() => {
              handleStreamToggle(StreamType.Keyboard, !enableKeyboard, setEnableKeyboard);
            }}
            className="flex items-center gap-2 px-3 py-2 rounded-lg transition-all cursor-pointer"
            style={{ 
              backgroundColor: enableKeyboard ? 'var(--app-success-bg)' : 'var(--app-danger-bg)',
              color: enableKeyboard ? 'var(--app-success)' : 'var(--app-danger)'
            }}
          >
            <Keyboard size={16} />
            <span className="text-xs font-semibold">Keyboard</span>
          </motion.button>

          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={() => {
              handleStreamToggle(StreamType.Clipboard, !enableClipboard, setEnableClipboard);
            }}
            className="flex items-center gap-2 px-3 py-2 rounded-lg transition-all cursor-pointer"
            style={{
              backgroundColor: enableClipboard ? 'var(--app-success-bg)' : 'var(--app-danger-bg)',
              color: enableClipboard ? 'var(--app-success)' : 'var(--app-danger)'
            }}
          >
            <Clipboard size={16} />
            <span className="text-xs font-semibold">Clipboard</span>
          </motion.button>
        </div>
      </motion.div>

      {/* Action Buttons */}
      <div className="grid grid-cols-3 gap-2">
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={handleToggleClients}
          className="p-3 rounded-lg transition-all duration-300 flex flex-col items-center gap-1 border-2"
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
          className="p-3 rounded-lg transition-all duration-300 flex flex-col items-center gap-1 border-2"
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
          className="p-3 rounded-lg transition-all duration-300 flex flex-col items-center gap-1 border-2"
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
                  className="w-full p-3 rounded-lg focus:outline-none transition-colors"
                  style={{
                    backgroundColor: 'var(--app-input-bg)',
                    border: '2px solid var(--app-input-border)',
                    color: 'var(--app-text-primary)'
                  }}
                  onFocus={(e) => e.currentTarget.style.borderColor = 'var(--app-primary)'}
                  onBlur={(e) => e.currentTarget.style.borderColor = 'var(--app-input-border)'}
                />
                {/* Position Selector */}
                <select
                  value={newClientPosition}
                  onChange={(e) => setNewClientPosition(e.target.value as 'top' | 'bottom' | 'left' | 'right')}
                  className="w-full h-9 p-5 rounded-lg focus:outline-none transition-colors"
                  style={{
                    backgroundColor: 'var(--app-input-bg)',
                    border: '2px solid var(--app-input-border)',
                    color: 'var(--app-text-primary)'
                  }}
                  onFocus={(e) => e.currentTarget.style.borderColor = 'var(--app-primary)'}
                  onBlur={(e) => e.currentTarget.style.borderColor = 'var(--app-input-border)'}
                >
                  <option value="top">Top</option>
                  <option value="bottom">Bottom</option>
                  <option value="left">Left</option>
                  <option value="right">Right</option>
                </select>
                <motion.button
                  whileHover={{ scale: 1.02 }}
                  whileTap={{ scale: 0.98 }}
                  onClick={addClient}
                  className="w-full p-3 rounded-lg transition-all flex items-center justify-center gap-2"
                  style={{
                    backgroundColor: 'var(--app-primary)',
                    color: 'white'
                  }}
                >
                  <Plus size={20} />
                  Add Client
                </motion.button>
              </div>

              <div className="space-y-2 max-h-60 overflow-y-auto">
                {clientManager.clients.map(client => (
                  <motion.div
                    key={client.id}
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: 20 }}
                    className="p-3 rounded-lg border flex items-center justify-between"
                    style={{
                      backgroundColor: client.status === 'online' ? 'var(--app-success-bg)' : 'var(--app-input-bg)',
                      borderColor: client.status === 'online' ? 'var(--app-success)' : 'var(--app-input-border)'
                    }}
                  >
                  <div>
                      <p className="font-semibold"
                        style={{ color: 'var(--app-text-primary)' }}
                      >{client.name}</p>
                      <p className="text-sm"
                        style={{ color: 'var(--app-text-muted)' }}
                      >{client.ip}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="flex flex-col items-center gap-2">
                        <div className="flex items-center gap-2">
                          <span className="px-2 py-1 text-xs rounded"
                            style={{ 
                              backgroundColor: client.status === 'offline' ? 'var(--app-bg-tertiary)' : 'var(--app-input-bg)',
                              color: 'var(--app-primary-light)' 
                            }}
                          >
                            {client.position.charAt(0).toUpperCase() + client.position.slice(1)}
                          </span>
                          <span className="px-2 py-1 rounded text-xs"
                            style={{
                              backgroundColor: client.status === 'online' ? 'var(--app-success-bg)' : 'var(--app-bg-tertiary)',
                              color: client.status === 'online' ? 'var(--app-success)' : 'var(--app-text-muted)'
                            }}
                          >
                            {client.status}
                          </span>
                        </div>
                        { client.uid && (
                          <CopyableBadge
                            key={client.uid}
                            fullText={client.uid}
                            displayText={abbreviateText(client.uid, 2, 2)}
                            label=""
                            titleText={`Click to copy Client UID: ${client.uid}`}
                          />
                        ) }
                      </div>
                      <motion.button
                        whileHover={{ scale: 1.1 }}
                        whileTap={{ scale: 0.9 }}
                        onClick={() => removeClient(client.id)}
                        className="p-2 transition-colors"
                        style={{ color: 'var(--app-danger)' }}
                      >
                        <Trash2 size={16} />
                      </motion.button>
                    </div>
                  </motion.div>
                ))}
              </div>
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
                  <label htmlFor="requireSSL" className="flex items-center gap-2 cursor-pointer"
                    style={{ color: 'var(--app-text-primary)' }}
                  >
                    <Lock size={18} />
                    <span>Require SSL</span>
                  </label>
                  <input
                    type="checkbox"
                    id="requireSSL"
                    checked={requireSSL}
                    disabled={otp !== '' || otpRequested}
                    onChange={(e) => {
                      if (otp !== '' || otpRequested) return;
                      setRequireSSL(e.target.checked);
                      handleSaveOptions(host, port, e.target.checked);
                    }}
                    className="w-5 h-5 cursor-pointer"
                    style={{ accentColor: 'var(--app-primary)' }}
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
                      className="px-4 py-2 rounded-lg transition-all flex items-center gap-2"
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
                  className="w-full p-3 rounded-lg focus:outline-none transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{
                    backgroundColor: 'var(--app-input-bg)',
                    border: '2px solid var(--app-input-border)',
                    color: 'var(--app-text-primary)'
                  }}
                  onFocus={(e) => !isRunning && (e.currentTarget.style.borderColor = 'var(--app-primary)')}
                  onBlur={(e) => e.currentTarget.style.borderColor = 'var(--app-input-border)'}
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
                  className="w-full p-3 rounded-lg focus:outline-none transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{
                    backgroundColor: 'var(--app-input-bg)',
                    border: '2px solid var(--app-input-border)',
                    color: 'var(--app-text-primary)'
                  }}
                  onFocus={(e) => !isRunning && (e.currentTarget.style.borderColor = 'var(--app-primary)')}
                  onBlur={(e) => e.currentTarget.style.borderColor = 'var(--app-input-border)'}
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
                  className="w-full p-3 rounded-lg focus:outline-none transition-colors"
                  style={{
                    backgroundColor: 'var(--app-input-bg)',
                    border: '2px solid var(--app-input-border)',
                    color: 'var(--app-text-primary)'
                  }}
                  onFocus={(e) => e.currentTarget.style.borderColor = 'var(--app-primary)'}
                  onBlur={(e) => e.currentTarget.style.borderColor = 'var(--app-input-border)'}
                />
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}