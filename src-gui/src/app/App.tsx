import { useEffect, useState, useRef } from 'react';
import { ClientTab } from './components/client-tab';
import { ServerTab } from './components/server-tab';
import { Titlebar } from './components/titlebar';
import { motion } from 'motion/react';

import { EventType, CommandType, ServiceStatus, ServerStatus, ClientStatus } from './api/Interface';

import { chooseService, getStatus } from './api/Sender';
import { listenCommand } from './api/Listener';
import { useEventListeners } from './hooks/useEventListeners';
import { useAppSelector, useAppDispatch } from './hooks/redux';
import { ActionType } from './store/actions';
import { ScrollArea } from './components/ui/scrollbar';


export default function App() {
  const [mode, setMode] = useState<'client' | 'server'>('client');
  const [disableModeSwitch, setDisableModeSwitch] = useState<boolean>(false);
  const [stateListenersAdded, setListenersAdded] = useState<boolean>(false);
  const listeners = useEventListeners();

  const serverState = useAppSelector(state => state.server);
  const clientState = useAppSelector(state => state.client);
  const dispatch = useAppDispatch();

  const isStartupRef = useRef(true);

  function firstStartup() {
      let isStartup = isStartupRef.current;
      if (isStartup) {
        console.log('[App] First startup detected, choosing service and setting up listeners');
        setupStatusListener();
        isStartupRef.current = false;
        chooseService(mode).catch((err) => {
          console.error('Error changing service:', err);
          listeners.forceRemoveListener('service-choice');
        });

        getStatus().catch((err) => {
          console.error('Error fetching status:', err);
        });
      }
  }

  function setupStatusListener() {
      setListenersAdded(true);
      listenCommand(EventType.CommandSuccess, CommandType.Status, (event) => {
            console.log(`Status received`, event);
            let result = event.data?.result as ServiceStatus;
            let server_status = result.server_info as ServerStatus;
            let client_status = result.client_info as ClientStatus;
            if (server_status) {
              if (server_status.running) {
                setMode('server');
              }
              // Dispatch action to update server state
              dispatch({type: ActionType.SERVER_STATE, payload: server_status});
            }
            if (client_status) {
              // Update client state in the store
              if (client_status.running) {
                setMode('client');
              }
              // Dispatch action to update client state
              dispatch({type: ActionType.CLIENT_STATE, payload: client_status});
            }
            listeners.removeListener('status');
          }).then((unlisten) => {
              listeners.addListenerOnce('status', () => {
                console.log('Removing status listener');
                unlisten();
                setListenersAdded(false);
              });
          });
  }

  useEffect(() => {
    firstStartup();
  }, []);

  useEffect(() => {
    if (!stateListenersAdded) {
      console.log('Setting up event listeners');
      return () => {
        setupStatusListener();
        
        getStatus().catch((err) => {
          console.error('Error fetching status:', err);
        });
      };
    }

  }, [mode]);

  function changeMode(newMode: 'client' | 'server', force: boolean = false) {
    console.log(`Changing mode to ${newMode} (force: ${force}, previous: ${mode})`);
    if (newMode === mode && !force) return;

    listenCommand(EventType.CommandSuccess, CommandType.ServiceChoice, (event) => {         
      console.log(`Service choice changed successfully: ${event.message}`);
      let mode = event.message?.toLowerCase();
      if (mode === 'client' || mode === 'server') {
        setMode(mode);
      }
      listeners.removeListener('service-choice');
    }).then((unlisten) => {
        listeners.addListenerOnce('service-choice', unlisten);
    });

    chooseService(newMode).catch((err) => {
      console.error('Error changing service:', err);
      listeners.forceRemoveListener('service-choice');
    });
  }
  
  return (
    <div className="min-h-screen w-full flex items-center justify-center" style={{ backgroundColor: 'var(--app-bg)' }}>
      <div className="w-[435px] h-[600px] overflow-hidden flex flex-col" style={{ backgroundColor: 'var(--app-bg-secondary)', borderColor: 'var(--app-border)' }}>
        {/* Titlebar */}
        <Titlebar disabled={disableModeSwitch} mode={mode} onModeChange={(newMode) => {
          changeMode(newMode);

          // Fetch status after changing service
          getStatus().catch((err) => {
            console.error('Error fetching status:', err);
            listeners.forceRemoveListener('service-choice');
          });
        }} />
        {/* Scrollable Content */}
        <ScrollArea extraPadding='pl-10' className={`flex-1 rounded-lg overflow-y-auto px-8 py-6 relative`}>
          {/* Content */}
          <motion.div 
            key={mode}
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.3 }}
          >
            {mode === 'client' ? <ClientTab onStatusChange={setDisableModeSwitch} state={clientState}/> : <ServerTab onStatusChange={setDisableModeSwitch} state={serverState}/>}
          </motion.div>
        </ScrollArea>
      </div>
    </div>
  );
}