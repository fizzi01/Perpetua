import { useState, useEffect } from 'react';
import { Power, Settings, Wifi, Clock, Key, MousePointer, Keyboard, Shield, Server } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { InlineNotification, Notification } from './inline-notification';
import { ClientTabProps } from '../commons/Tab';
import { ClientStatus, CommandType, EventType, ServerChoice } from '../api/Interface';
import { listenCommand, listenGeneralEvent } from '../api/Listener';
import { startClient, stopClient } from '../api/Sender';
import { useEventListeners } from '../hooks/useEventListeners';

export function ClientTab({ onStatusChange, state }: ClientTabProps) {
  let previousState: ClientStatus | null = null;

  const [runningPending, setRunningPending] = useState(false);
  const [isRunning, setIsRunning] = useState(state.running);
  const [isConnected, setIsConnected] = useState(state.connected);
  const [showOptions, setShowOptions] = useState(false);
  const [showOtpInput, setShowOtpInput] = useState(false);
  const [serverAddress, setServerAddress] = useState('192.168.1.1:8080');
  const [autoConnect, setAutoConnect] = useState(false);
  const [encryption, setEncryption] = useState('AES-256');
  const [enableMouse, setEnableMouse] = useState(true);
  const [enableKeyboard, setEnableKeyboard] = useState(true);
  const [otpInput, setOtpInput] = useState('');
  const [connectionTime, setConnectionTime] = useState(0);
  const [dataUsage, setDataUsage] = useState(0);
  const [controlStatus, setControlStatus] = useState<'none' | 'controlled' | 'idle'>('none');
  const [notifications, setNotifications] = useState<Notification[]>([]);

  const listeners = useEventListeners('client-tab');
  const connectionListeners = handleConnectionListeners();

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

  const ipAddress = isConnected ? '10.8.0.2' : 'Not Connected';

  useEffect(() => {
    if (!isConnected) return;

    const controlInterval = setInterval(() => {
      const random = Math.random();
      if (random > 0.9) {
        setControlStatus('controlled');
        addNotification('warning', 'Server is controlling', `${enableMouse ? 'Mouse' : ''}${enableMouse && enableKeyboard ? ' & ' : ''}${enableKeyboard ? 'Keyboard' : ''}`);
      } else if (random < 0.1) {
        setControlStatus('idle');
      }
    }, 12000);

    const timeInterval = setInterval(() => {
      setConnectionTime(prev => prev + 1);
    }, 1000);

    const dataInterval = setInterval(() => {
      setDataUsage(prev => prev + Math.random() * 30);
    }, 2000);

    return () => {
      clearInterval(controlInterval);
      clearInterval(timeInterval);
      clearInterval(dataInterval);
    };
  }, [isConnected, enableMouse, enableKeyboard]);

  function handleConnectionListeners() {

    const setup = () => {
      listenGeneralEvent(EventType.Connected, (event) => {
        setIsConnected(true);
        setShowOtpInput(false);
        addNotification('success', 'Connected', serverAddress);
      }).then((unlisten) => {
        listeners.addListenerOnce('client-connected', unlisten);
      });

      listenGeneralEvent(EventType.Disconnected, (event) => {
        setIsConnected(false);
        setConnectionTime(0);
        setDataUsage(0);
        setControlStatus('none');
        setShowOtpInput(false);
        setOtpInput('');
        addNotification('warning', 'Disconnected');
      }).then((unlisten) => {
        listeners.addListenerOnce('client-disconnected', unlisten);
      });

      listenGeneralEvent(EventType.ServerChoiceNeeded, (event) => {
        let res = event.data as ServerChoice;
        if (res) {
          
        }

      }).then((unlisten) => {
        listeners.addListenerOnce('server-choice-needed', unlisten);
      });

      listenGeneralEvent(EventType.OtpNeeded, (event) => {
        setShowOtpInput(true);
        listeners.removeListener('otp-needed');
      }).then((unlisten) => {
        listeners.addListenerOnce('otp-needed', unlisten);
      });
    }

    const cleanup = () => {
      listeners.forceRemoveListener('client-connected');
      listeners.forceRemoveListener('client-disconnected');
      listeners.forceRemoveListener('server-choice-needed');
      listeners.forceRemoveListener('otp-needed');
    }

    return { setup, cleanup };
  };

  const handleToggleClient = () => {
    // if (!isConnected) {
    //   setShowOtpInput(true);
    // } else {
    //   setIsConnected(false);
    //   setConnectionTime(0);
    //   setDataUsage(0);
    //   setControlStatus('none');
    //   setShowOtpInput(false);
    //   setOtpInput('');
    //   addNotification('warning', 'Disconnected');
    // }

    if (!isRunning) {
      setRunningPending(true);
      
      listenCommand(EventType.CommandSuccess, CommandType.StartClient, (event) => {
        console.log(`Client started successfully: ${event.message}`);
        setIsRunning(true);
        let res = event.data?.result;
        if (res) {
          let server_ip = res.ip_address as string;
          let port = res.port as number;
          setServerAddress(`${server_ip}:${port}`);
          addNotification('success', 'Connected', `${server_ip}:${port}`);
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

        listeners.removeListener('client-start-error');
        listeners.removeListener('client-start');
      }).then(unlisten => {
        listeners.addListenerOnce('client-start-error', unlisten);
      });

      startClient().then(() => {
        connectionListeners.setup();
      }).catch((err) => {
        console.error('Error invoking startClient:', err);
        addNotification('error', 'Connection Failed', err.message || 'Unknown error');
        setRunningPending(false);
        listeners.forceRemoveListener('client-start-error');
        listeners.forceRemoveListener('client-start');
      });

    } else {
      connectionListeners.cleanup();
    }
  };

  const handleOtpSubmit = () => {
    if (otpInput.length === 6) {
      setIsConnected(true);
      setShowOtpInput(false);
      addNotification('success', 'Connected', serverAddress);
    } else {
      addNotification('error', 'Invalid OTP', 'Enter 6-digit code');
    }
  };

  const formatTime = (seconds: number) => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
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
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          onClick={handleToggleClient}
          className="w-24 h-24 rounded-full flex items-center justify-center transition-all duration-300 shadow-lg relative overflow-hidden"
          style={{
            backgroundColor: isConnected ? 'var(--app-success)' : 'var(--app-bg-tertiary)',
            color: 'white'
          }}
        >
          {isConnected && (
            <motion.div
              className="absolute inset-0 opacity-30"
              style={{ backgroundColor: 'var(--app-success)' }}
              animate={{ scale: [1, 1.5, 1] }}
              transition={{ duration: 2, repeat: Infinity }}
            />
          )}
          <Power size={48} className="relative z-10" />
        </motion.button>
        <motion.p 
          key={isConnected ? 'connected' : 'disconnected'}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-3 font-semibold"
          style={{ color: 'var(--app-text-primary)' }}
        >
          {isConnected ? 'Connected' : 'Disconnected'}
        </motion.p>
      </div>

      {/* Inline Notifications */}
      <InlineNotification notifications={notifications} />

      {/* OTP Input */}
      <AnimatePresence>
        {showOtpInput && !isConnected && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
            className="overflow-hidden"
          >
            <div className="space-y-3 p-4 rounded-lg border"
              style={{ 
                backgroundColor: 'var(--app-card-bg)',
                borderColor: 'var(--app-card-border)'
              }}
            >
              <h3 className="font-semibold flex items-center gap-2"
                style={{ color: 'var(--app-text-primary)' }}
              >
                <Key size={18} />
                Enter OTP Code
              </h3>
              <input
                type="text"
                placeholder="000000"
                maxLength={6}
                value={otpInput}
                onChange={(e) => setOtpInput(e.target.value.replace(/\D/g, ''))}
                className="w-full p-3 rounded-lg focus:outline-none text-center text-2xl font-bold tracking-widest"
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
                onClick={handleOtpSubmit}
                className="w-full p-3 rounded-lg transition-all"
                style={{
                  backgroundColor: 'var(--app-primary)',
                  color: 'white'
                }}
              >
                Connect
              </motion.button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Disconnected Status Info */}
      {!isConnected && !showOtpInput && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.2 }}
          className="space-y-2"
        >
          <motion.div 
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
            <div className="flex-1">
              <div className="text-sm font-bold" style={{ color: 'var(--app-text-primary)' }}>
                {serverAddress}
              </div>
              <div className="text-xs" style={{ color: 'var(--app-text-muted)' }}>Server Address</div>
            </div>
          </motion.div>

          <div className="flex gap-2">
            <motion.div 
              whileHover={{ scale: 1.02 }}
              className="flex-1 flex items-center gap-3 p-4 rounded-lg border backdrop-blur-sm"
              style={{ 
                backgroundColor: 'var(--app-card-bg)',
                borderColor: 'var(--app-card-border)'
              }}
            >
              <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ backgroundColor: 'var(--app-bg-tertiary)' }}>
                <Shield size={20} style={{ color: 'var(--app-text-muted)' }} />
              </div>
              <div className="flex-1">
                <div className="text-sm font-bold" style={{ color: 'var(--app-text-primary)' }}>
                  {encryption}
                </div>
                <div className="text-xs" style={{ color: 'var(--app-text-muted)' }}>Encryption</div>
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
          </div>
        </motion.div>
      )}

      {/* Connection Info */}
      {isConnected && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.2 }}
          className="space-y-2"
        >
          <div className="flex gap-2">
            <motion.div 
              whileHover={{ scale: 1.02 }}
              className="flex-1 flex items-center gap-3 p-4 rounded-lg border backdrop-blur-sm"
              style={{ 
                backgroundColor: 'var(--app-card-bg)',
                borderColor: 'var(--app-card-border)'
              }}
            >
              <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ backgroundColor: 'var(--app-primary)' }}>
                <Wifi size={20} style={{ color: 'white' }} />
              </div>
              <div className="flex-1">
                <div className="text-sm font-bold" style={{ color: 'var(--app-text-primary)' }}>
                  {ipAddress}
                </div>
                <div className="text-xs" style={{ color: 'var(--app-text-muted)' }}>IP Address</div>
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
                <Clock size={20} style={{ color: 'white' }} />
              </div>
              <div className="flex-1">
                <div className="text-sm font-bold" style={{ color: 'var(--app-text-primary)' }}>
                  {formatTime(connectionTime)}
                </div>
                <div className="text-xs" style={{ color: 'var(--app-text-muted)' }}>Duration</div>
              </div>
            </motion.div>
          </div>

          {/* Data Usage - Full Width */}
          <motion.div 
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
          </motion.div>

          {/* Control Status */}
          {controlStatus !== 'none' && (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
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
      <motion.div 
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.3 }}
        whileHover={{ scale: 1.01 }}
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
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg"
            style={{ 
              backgroundColor: enableMouse ? 'var(--app-success-bg)' : 'var(--app-danger-bg)',
              color: enableMouse ? 'var(--app-success)' : 'var(--app-danger)'
            }}
          >
            <MousePointer size={16} />
            <span className="text-xs font-semibold">Mouse</span>
          </div>
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg"
            style={{ 
              backgroundColor: enableKeyboard ? 'var(--app-success-bg)' : 'var(--app-danger-bg)',
              color: enableKeyboard ? 'var(--app-success)' : 'var(--app-danger)'
            }}
          >
            <Keyboard size={16} />
            <span className="text-xs font-semibold">Keyboard</span>
          </div>
        </div>
      </motion.div>

      {/* Settings Button */}
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
      </motion.button>

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
                Client Settings
              </h3>

              <div>
                <label className="block mb-2 font-semibold"
                  style={{ color: 'var(--app-text-primary)' }}
                >Server Address</label>
                <input
                  type="text"
                  value={serverAddress}
                  onChange={(e) => setServerAddress(e.target.value)}
                  className="w-full p-3 rounded-lg focus:outline-none transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{
                    backgroundColor: 'var(--app-input-bg)',
                    border: '2px solid var(--app-input-border)',
                    color: 'var(--app-text-primary)'
                  }}
                  onFocus={(e) => !isConnected && (e.currentTarget.style.borderColor = 'var(--app-primary)')}
                  onBlur={(e) => e.currentTarget.style.borderColor = 'var(--app-input-border)'}
                  disabled={isConnected}
                />
              </div>

              <div>
                <label className="block mb-2 font-semibold"
                  style={{ color: 'var(--app-text-primary)' }}
                >Encryption</label>
                <select
                  value={encryption}
                  onChange={(e) => setEncryption(e.target.value)}
                  className="w-full p-3 rounded-lg focus:outline-none transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{
                    backgroundColor: 'var(--app-input-bg)',
                    border: '2px solid var(--app-input-border)',
                    color: 'var(--app-text-primary)'
                  }}
                  onFocus={(e) => !isConnected && (e.currentTarget.style.borderColor = 'var(--app-primary)')}
                  onBlur={(e) => e.currentTarget.style.borderColor = 'var(--app-input-border)'}
                  disabled={isConnected}
                >
                  <option value="AES-256">AES-256</option>
                  <option value="AES-128">AES-128</option>
                  <option value="ChaCha20">ChaCha20</option>
                </select>
              </div>

              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <label htmlFor="autoConnect" className="flex items-center gap-2 cursor-pointer"
                    style={{ color: 'var(--app-text-primary)' }}
                  >
                    <span>Auto-connect on startup</span>
                  </label>
                  <input
                    type="checkbox"
                    id="autoConnect"
                    checked={autoConnect}
                    onChange={(e) => {
                      setAutoConnect(e.target.checked);
                      addNotification('info', `Auto-connect ${e.target.checked ? 'enabled' : 'disabled'}`);
                    }}
                    className="w-5 h-5 cursor-pointer"
                    style={{ accentColor: 'var(--app-primary)' }}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <label htmlFor="enableMouseClient" className="flex items-center gap-2 cursor-pointer"
                    style={{ color: 'var(--app-text-primary)' }}
                  >
                    <MousePointer size={18} />
                    <span>Allow Mouse Control</span>
                  </label>
                  <input
                    type="checkbox"
                    id="enableMouseClient"
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
                  <label htmlFor="enableKeyboardClient" className="flex items-center gap-2 cursor-pointer"
                    style={{ color: 'var(--app-text-primary)' }}
                  >
                    <Keyboard size={18} />
                    <span>Allow Keyboard Control</span>
                  </label>
                  <input
                    type="checkbox"
                    id="enableKeyboardClient"
                    checked={enableKeyboard}
                    onChange={(e) => {
                      setEnableKeyboard(e.target.checked);
                      addNotification('info', `Keyboard control ${e.target.checked ? 'enabled' : 'disabled'}`);
                    }}
                    className="w-5 h-5 cursor-pointer"
                    style={{ accentColor: 'var(--app-primary)' }}
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