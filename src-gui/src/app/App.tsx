import { useState } from 'react';
import { ClienTab } from './components/client-tab';
import { ServerTab } from './components/server-tab';
import { Titlebar } from './components/titlebar';
import { motion } from 'motion/react';
import { EventType, CommandType } from './api/Interface';

import { chooseService } from './api/Sender';
import { listenCommand } from './api/Listener';
import { useEventListeners } from './hooks/useEventListeners';

export default function App() {
  const [mode, setMode] = useState<'client' | 'server'>('client');
  const [disableModeSwitch, setDisableModeSwitch] = useState<boolean>(false);
  const listeners = useEventListeners();

  return (
    <div className="min-h-screen w-full flex items-center justify-center" style={{ backgroundColor: 'var(--app-bg)' }}>
      <div className="w-[450px] h-[600px] rounded-lg overflow-hidden flex flex-col" style={{ backgroundColor: 'var(--app-bg-secondary)', borderColor: 'var(--app-border)' }}>
        {/* Titlebar */}
        <Titlebar disabled={disableModeSwitch} mode={mode} onModeChange={(newMode) => {
          listenCommand(EventType.CommandSuccess, CommandType.ServiceChoice, (event) => {
              console.log(`Service choice changed successfully: ${event.message}`);
              let mode = event.message?.toLowerCase();
              if (mode === 'client' || mode === 'server') {
                setMode(mode);
              }

              listeners.removeListener('service-choice');
          }).then((unlisten) => {
              listeners.addListener('service-choice', unlisten);
          });

          chooseService(newMode).catch((err) => {
            console.error('Error changing service:', err);
            listeners.removeListener('service-choice');
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
            {mode === 'client' ? <ClienTab onStatusChange={setDisableModeSwitch} /> : <ServerTab onStatusChange={setDisableModeSwitch}/>}
          </motion.div>
        </div>
      </div>
    </div>
  );
}