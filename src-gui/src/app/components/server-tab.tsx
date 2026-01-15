import { useState, useEffect, useRef} from 'react';
import { Power, Settings, Users, Activity, Plus, Trash2, Key, Lock, MousePointer, Keyboard, Shield, Clipboard } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { InlineNotification, Notification } from './inline-notification';

import { useEventListeners } from '../hooks/useEventListeners';
import { shareCertificate, startServer, stopServer } from '../api/Sender';
import { listenCommand, listenGeneralEvent } from '../api/Listener';
import { EventType, CommandType, ClientObj, StreamType, ServerStatus, OtpInfo} from '../api/Interface';

import { ServerTabProps } from '../commons/Tab'
import { parseStreams } from '../api/Utility';
import { listen } from '@tauri-apps/api/event';

interface Client {
  id: string;
  name: string;
  ip: string;
  status: 'online' | 'offline';
  position: 'top' | 'bottom' | 'left' | 'right';
  connectedAt?: Date;
}

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
  const [acceptedClients, setAcceptedClients] = useState<Client[]>([]);
  const [newClientName, setNewClientName] = useState('');
  const [newClientIp, setNewClientIp] = useState('');
  const [newClientPosition, setNewClientPosition] = useState<'top' | 'bottom' | 'left' | 'right'>('top');
  const [connectedClients, setConnectedClients] = useState(0);
  const [uptime, setUptime] = useState(0);
  const [notifications, setNotifications] = useState<Notification[]>([]);

  const listeners = useEventListeners('server-tab');

  const otpFocus = useRef<HTMLDivElement>(null);

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
// }, [isRunning, acceptedClients, connectedClients, maxClients]);
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
    if (notify) {
      addNotification(
        connected ? 'success' : 'warning', 
        connected ? 'Client Connected' : 'Client Disconnected', 
        `${clientData.host_name ? clientData.host_name : clientData.ip_address} (${clientData.screen_position.toUpperCase()})`
      );
    }
    // Map to Client interface
    setAcceptedClients(prev => {
      const existingClient = prev.find(c => c.id === clientData.uid);
      console.log('Existing client:', existingClient);
      if (existingClient) {
        return prev.map(c =>
          c.id === clientData.uid ? { ...c, status: connected ? 'online' : 'offline', connectedAt: connected ? new Date() : c.connectedAt } : c
        );
      } else {
        console.log('Adding new client to acceptedClients');
        const newClient: Client = {
          id: clientData.uid,
          name: clientData.host_name ? clientData.host_name : clientData.uid,
          ip: clientData.ip_address,
          status: connected ? 'online' : 'offline',
          position: clientData.screen_position as 'top' | 'bottom' | 'left' | 'right', 
        };
        return [...prev, newClient];
      }
    });

    setConnectedClients(prev => {
      return connected ? prev + 1 : Math.max(prev - 1, 0);
    });
  };

  const handleClientEventListeners = () => {
      listenGeneralEvent(EventType.ClientConnected, (event) => {
        // Handle client connected event here
        let client_data = event.data as ClientObj;
        handleClientConnected(client_data, true, true);
      }).then(unlisten => {
        listeners.addListener('client-connected', unlisten);
      });

      listenGeneralEvent(EventType.ClientDisconnected, (event) => {
        // Handle client disconnected event here
        let client_data = event.data as ClientObj;
        handleClientConnected(client_data, false, true);
      }).then(unlisten => {
        listeners.addListener('client-disconnected', unlisten);
      });
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

      handleClientEventListeners();

      startServer().catch((err) => {
        console.error('Error starting server:', err);
        addNotification('error', 'Failed to start server');
        setRunningPending(false);

        // Cleanup
        listeners.removeListener('start-server');
        listeners.removeListener('start-server-error');
        listeners.removeListener('client-connected');
        listeners.removeListener('client-disconnected');
      });
      
    } else {
      setRunningPending(true);

      listeners.removeListener('client-connected');
      listeners.removeListener('client-disconnected');

      // Setup one-time listener per lo stop del server
      listenCommand(EventType.CommandSuccess, CommandType.StopServer, (event) => {
        console.log(`Server stopped successfully: ${event.message}`);
        setIsRunning(false);
        setAcceptedClients(prev => prev.map(c => ({ ...c, status: 'offline' })));
        setConnectedClients(0);
        setUptime(0);
        setOtp('');
        addNotification('warning', 'Server stopped');
        onStatusChange(false);
        setRunningPending(false);
        
        // Auto-unlisten dopo la prima esecuzione
        listeners.removeListener('stop-server');
      }).then(unlisten => {
        listeners.addListener('stop-server', unlisten);
      });

      stopServer().catch((err) => {
        console.error('Error stopping server:', err);
        addNotification('error', 'Failed to stop server');
        setRunningPending(false);
        // Cleanup listener in caso di errore
        listeners.removeListener('stop-server');
      });
    }
  };

  const generateOtp = () => {
    if (otp !== '') return;
    if (otpRequested) return;
    // const newOtp = Math.floor(100000 + Math.random() * 900000).toString();
    // setOtp(newOtp);
    // addNotification('success', 'OTP Generated', `Code: ${newOtp}`);

    // setTimeout(() => {
    //   setOtp('');
    //   addNotification('info', 'OTP Expired');
    // }, otpTimeout * 1000);

    // // Scroll to the OTP display
    // setTimeout(() => {
    // otpFocus.current?.scrollIntoView({ behavior: 'smooth' });
    // }, 5);
    
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
    if (!newClientName || !newClientIp || !newClientPosition) {
      addNotification('error', 'Missing information');
      return;
    }

    const newClient: Client = {
      id: Date.now().toString(),
      name: newClientName,
      ip: newClientIp,
      status: 'offline',
      position: newClientPosition,
    };

    setAcceptedClients(prev => [...prev, newClient]);
    setNewClientName('');
    setNewClientIp('');
    setNewClientPosition('top');
    addNotification('success', `${newClientName} added`);
  };

  const removeClient = (id: string) => {
    const client = acceptedClients.find(c => c.id === id);
    setAcceptedClients(prev => prev.filter(c => c.id !== id));
    if (client?.status === 'online') {
      setConnectedClients(prev => prev - 1);
    }
    addNotification('info', `${client?.name} removed`);
  };

  const formatUptime = (seconds: number) => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  };

  return (
    <div className="space-y-5">
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
              key={connectedClients}
              initial={{ scale: 1.3 }}
              animate={{ scale: 1 }}
              className="text-xl font-bold"
              style={{ color: 'var(--app-text-primary)' }}
            >
              {connectedClients}
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
              setEnableMouse(!enableMouse);
              // addNotification('info', `Mouse control ${!enableMouse ? 'enabled' : 'disabled'}`);
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
              setEnableKeyboard(!enableKeyboard);
              // addNotification('info', `Keyboard control ${!enableKeyboard ? 'enabled' : 'disabled'}`);
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
              setEnableClipboard(!enableClipboard);
              // addNotification('info', `Clipboard control ${!enableClipboard ? 'enabled' : 'disabled'}`);
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
            <div className="space-y-4 p-4 rounded-lg border"
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
                <input
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
                />
                <input
                  type="text"
                  placeholder="IP Address"
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
                {acceptedClients.map(client => (
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
                  onChange={(e) => setHost(e.target.value)}
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
                  onChange={(e) => setPort(e.target.value)}
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
                  onChange={(e) => setOtpTimeout(parseInt(e.target.value))}
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