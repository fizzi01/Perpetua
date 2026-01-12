import { useState } from 'react';
import { VpnClient } from './components/client-tab';
import { VpnServer } from './components/server-tab';
import { Titlebar } from './components/titlebar';
import { motion } from 'motion/react';
import { listen } from '@tauri-apps/api/event';
import { invoke } from '@tauri-apps/api/core';

listen('event', (event) => {
  console.log('Received event:', event);
});

interface CommandPayload {
  data?: {
    command?: string;
  };
  message?: string;
}

export default function App() {
  const [mode, setMode] = useState<'client' | 'server'>('client');

  listen('command-success', (event) => {
    console.log('Command succeeded:', event);
    let payload = event.payload as CommandPayload;
    // Access data from payload object
    let data = payload?.data;
    let message = payload?.message;
    if (data && message) {
      if (data?.command == 'service_choice') {
        console.log(`Service choice changed successfully: ${message}`);
        let mode = message.toLowerCase();
        if (mode === 'client' || mode === 'server') {
          setMode(mode);
        }
      }
    }
  });

  return (
    <div className="min-h-screen w-full flex items-center justify-center" style={{ backgroundColor: 'var(--app-bg)' }}>
      <div className="w-[450px] h-[600px] rounded-lg overflow-hidden flex flex-col" style={{ backgroundColor: 'var(--app-bg-secondary)', borderColor: 'var(--app-border)' }}>
        {/* Titlebar */}
        <Titlebar mode={mode} onModeChange={(newMode) => {
          invoke('choose_service', { service: newMode }).catch((err) => {
            console.error('Error changing service:', err);
          });
        }} />
        
        {/* Scrollable Content */}
        <div className="flex-1 overflow-y-auto px-8 py-6 relative">
          {/* Content */}
          <motion.div 
            key={mode}
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.3 }}
          >
            {mode === 'client' ? <VpnClient /> : <VpnServer />}
          </motion.div>
        </div>
      </div>
    </div>
  );
}