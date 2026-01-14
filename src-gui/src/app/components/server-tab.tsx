import { useState, useEffect, useRef } from 'react';
import { Power, Settings, Users, Signal, Activity, Plus, Trash2, Key, MousePointer, Keyboard, Shield } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { InlineNotification, Notification } from './inline-notification';

import { useEventListeners } from '../hooks/useEventListeners';
import { startServer, stopServer } from '../api/Sender';
import { listenCommand } from '../api/Listener';
import { EventType, CommandType} from '../api/Interface';

interface Client {
  id: string;
  name: string;
  ip: string;
  status: 'online' | 'offline';
  position: 'top' | 'bottom' | 'left' | 'right';
  connectedAt?: Date;
}

export function ServerTab({ onStatusChange }: TabProps) {
  const [runningPending, setRunningPending] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [showOptions, setShowOptions] = useState(false);
  const [showClients, setShowClients] = useState(false);
  const [showSecurity, setShowSecurity] = useState(false);
  const [port, setPort] = useState('8080');
  const [protocol, setProtocol] = useState('TCP');
  const [maxClients, setMaxClients] = useState('10');
  const [enableMouse, setEnableMouse] = useState(true);
  const [enableKeyboard, setEnableKeyboard] = useState(true);
  const [requireOtp, setRequireOtp] = useState(false);
  const [otp, setOtp] = useState('');
  const [otpTimeout, setOtpTimeout] = useState(300);
  const [acceptedClients, setAcceptedClients] = useState<Client[]>([
    { id: '1', name: 'Laptop-Office', ip: '192.168.1.100', status: 'offline', position: 'top' },
    { id: '2', name: 'Desktop-Home', ip: '192.168.1.101', status: 'offline', position: 'bottom' },
  ]);
  const [newClientName, setNewClientName] = useState('');
  const [newClientIp, setNewClientIp] = useState('');
  const [newClientPosition, setNewClientPosition] = useState<'top' | 'bottom' | 'left' | 'right'>('top');
  const [connectedClients, setConnectedClients] = useState(0);
  const [uptime, setUptime] = useState(0);
  const [dataTransferred, setDataTransferred] = useState(0);
  const [notifications, setNotifications] = useState<Notification[]>([]);

  const listeners = useEventListeners();

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

    const clientInterval = setInterval(() => {
      const random = Math.random();
      const offlineClients = acceptedClients.filter(c => c.status === 'offline');
      const onlineClients = acceptedClients.filter(c => c.status === 'online');
      
      if (random > 0.85 && offlineClients.length > 0 && connectedClients < parseInt(maxClients)) {
        const client = offlineClients[Math.floor(Math.random() * offlineClients.length)];
        setAcceptedClients(prev => prev.map(c => 
          c.id === client.id ? { ...c, status: 'online', connectedAt: new Date() } : c
        ));
        setConnectedClients(prev => prev + 1);
        addNotification('success', `${client.name} connected`, client.ip);
      } else if (random < 0.15 && onlineClients.length > 0) {
        const client = onlineClients[Math.floor(Math.random() * onlineClients.length)];
        setAcceptedClients(prev => prev.map(c => 
          c.id === client.id ? { ...c, status: 'offline' } : c
        ));
        setConnectedClients(prev => prev - 1);
        addNotification('warning', `${client.name} disconnected`, client.ip);
      }
    }, 8000);

    const uptimeInterval = setInterval(() => {
      setUptime(prev => prev + 1);
    }, 1000);

    const dataInterval = setInterval(() => {
      setDataTransferred(prev => prev + Math.random() * 50);
    }, 2000);

    return () => {
      clearInterval(clientInterval);
      clearInterval(uptimeInterval);
      clearInterval(dataInterval);
    };
  }, [isRunning, acceptedClients, connectedClients, maxClients]);

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
      }).then(unlisten => {
        listeners.addListener('start-server', unlisten);
      });

      startServer().catch((err) => {
        console.error('Error starting server:', err);
        addNotification('error', 'Failed to start server');
        setRunningPending(false);

        // Cleanup
        listeners.removeListener('start-server');
      });
      
    } else {
      setRunningPending(true);

      // Setup one-time listener per lo stop del server
      listenCommand(EventType.CommandSuccess, CommandType.StopServer, (event) => {
        console.log(`Server stopped successfully: ${event.message}`);
        setIsRunning(false);
        setAcceptedClients(prev => prev.map(c => ({ ...c, status: 'offline' })));
        setConnectedClients(0);
        setUptime(0);
        setDataTransferred(0);
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
    const newOtp = Math.floor(100000 + Math.random() * 900000).toString();
    setOtp(newOtp);
    addNotification('success', 'OTP Generated', `Code: ${newOtp}`);

    setTimeout(() => {
      setOtp('');
      addNotification('info', 'OTP Expired');
    }, otpTimeout * 1000);
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

  const formatData = (mb: number) => {
    if (mb >= 1024) {
      return `${(mb / 1024).toFixed(2)} GB`;
    }
    return `${mb.toFixed(0)} MB`;
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

      {/* Data Transfer - Full Width */}
      <motion.div 
        whileHover={{ scale: 1.01 }}
        className="flex items-center gap-3 p-4 rounded-lg border backdrop-blur-sm"
        style={{ 
          backgroundColor: 'var(--app-card-bg)',
          borderColor: 'var(--app-card-border)'
        }}
      >
        <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ backgroundColor: 'var(--app-primary)' }}>
          <Signal size={20} style={{ color: 'white' }} />
        </div>
        <div className="flex-1">
          <div className="text-xl font-bold" style={{ color: 'var(--app-text-primary)' }}>
            {formatData(dataTransferred)}
          </div>
          <div className="text-xs" style={{ color: 'var(--app-text-muted)' }}>Data Transferred</div>
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
                  <label htmlFor="enableMouse" className="flex items-center gap-2 cursor-pointer"
                    style={{ color: 'var(--app-text-primary)' }}
                  >
                    <MousePointer size={18} />
                    <span>Allow Mouse Control</span>
                  </label>
                  <input
                    type="checkbox"
                    id="enableMouse"
                    checked={enableMouse}
                    onChange={(e) => {
                      setEnableMouse(e.target.checked);
                      addNotification('info', `Mouse control ${e.target.checked ? 'enabled' : 'disabled'}`);
                    }}
                    className="w-5 h-5 cursor-pointer"
                    style={{ accentColor: 'var(--app-primary)' }}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <label htmlFor="enableKeyboard" className="flex items-center gap-2 cursor-pointer"
                    style={{ color: 'var(--app-text-primary)' }}
                  >
                    <Keyboard size={18} />
                    <span>Allow Keyboard Control</span>
                  </label>
                  <input
                    type="checkbox"
                    id="enableKeyboard"
                    checked={enableKeyboard}
                    onChange={(e) => {
                      setEnableKeyboard(e.target.checked);
                      addNotification('info', `Keyboard control ${e.target.checked ? 'enabled' : 'disabled'}`);
                    }}
                    className="w-5 h-5 cursor-pointer"
                    style={{ accentColor: 'var(--app-primary)' }}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <label htmlFor="requireOtp" className="flex items-center gap-2 cursor-pointer"
                    style={{ color: 'var(--app-text-primary)' }}
                  >
                    <Key size={18} />
                    <span>Require OTP</span>
                  </label>
                  <input
                    type="checkbox"
                    id="requireOtp"
                    checked={requireOtp}
                    onChange={(e) => {
                      setRequireOtp(e.target.checked);
                      addNotification('info', `OTP ${e.target.checked ? 'required' : 'optional'}`);
                    }}
                    className="w-5 h-5 cursor-pointer"
                    style={{ accentColor: 'var(--app-primary)' }}
                  />
                </div>
              </div>

              {requireOtp && isRunning && (
                <motion.div
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="pt-4 space-y-3"
                  style={{ borderTop: '1px solid var(--app-border)' }}
                >
                  <div className="flex items-center justify-between">
                    <span style={{ color: 'var(--app-text-primary)' }}>One-Time Password</span>
                    <motion.button
                      whileHover={{ scale: 1.05 }}
                      whileTap={{ scale: 0.95 }}
                      onClick={generateOtp}
                      className="px-4 py-2 rounded-lg transition-all flex items-center gap-2"
                      style={{
                        backgroundColor: 'var(--app-primary)',
                        color: 'white'
                      }}
                    >
                      <Key size={16} />
                      Generate
                    </motion.button>
                  </div>
                  {otp && (
                    <motion.div
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
                >Protocol</label>
                <select
                  value={protocol}
                  onChange={(e) => setProtocol(e.target.value)}
                  className="w-full p-3 rounded-lg focus:outline-none transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{
                    backgroundColor: 'var(--app-input-bg)',
                    border: '2px solid var(--app-input-border)',
                    color: 'var(--app-text-primary)'
                  }}
                  onFocus={(e) => !isRunning && (e.currentTarget.style.borderColor = 'var(--app-primary)')}
                  onBlur={(e) => e.currentTarget.style.borderColor = 'var(--app-input-border)'}
                  disabled={isRunning}
                >
                  <option value="TCP">TCP</option>
                  <option value="UDP">UDP</option>
                </select>
              </div>

              <div>
                <label className="block mb-2 font-semibold"
                  style={{ color: 'var(--app-text-primary)' }}
                >Max Clients</label>
                <input
                  type="text"
                  value={maxClients}
                  onChange={(e) => setMaxClients(e.target.value)}
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