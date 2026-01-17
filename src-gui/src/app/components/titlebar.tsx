import { motion } from 'motion/react';
import { WindowTitlebar } from '../../tauri-controls/window-titlebar';
import { platform } from '@tauri-apps/plugin-os';

interface TitlebarProps {
  disabled?: boolean;
  mode: 'client' | 'server';
  onModeChange: (mode: 'client' | 'server') => void;
}

interface InnerBarProps extends TitlebarProps {
  justify_mode: "between" | "center";
}

export function InnerBar({ disabled, mode, justify_mode, onModeChange }: InnerBarProps) {
  return (<div 
        data-tauri-drag-region
        className={`titlebar w-full px-6 py-1.5 flex items-center justify-${justify_mode} border-b backdrop-blur-md`}
      >
        <div></div>
        {/* Mode Toggle */}
        <motion.div 
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.3 }}
          className="titlebar-toggle w-[200px] rounded-md p-0.5 flex shadow-sm border h-full"
        >
          <button
            disabled={disabled}
            onClick={() => onModeChange('client')}
            className={`titlebar-button py-1.5 px-4 rounded-sm font-medium text-xs tracking-wide h-full ${
              mode === 'client' ? 'active' : ''
            } ${disabled ? 'opacity-50' : ''}`}
          >
            CLIENT
          </button>
          <button
            disabled={disabled}
            onClick={() => onModeChange('server')}
            className={`titlebar-button py-1.5 px-4 rounded-sm font-medium text-xs tracking-wide h-full ${
              mode === 'server' ? 'active' : ''
            } ${disabled ? 'opacity-50' : ''}`}
          >
            SERVER
          </button>
        </motion.div>
      </div>);
}


export function Titlebar({ disabled, mode, onModeChange }: TitlebarProps) {

  const currentPlatform = platform() as 'windows' | 'macos' | 'linux' | 'unknown';

  return (
    currentPlatform === "windows" ? (
      <WindowTitlebar className='titlebar w-full' windowControlsProps={{
        platform: 'windows',
        className: 'titlebar-system-group h-full border-b backdrop-blur-md',
      }}>
        <InnerBar disabled={disabled} mode={mode} justify_mode='between' onModeChange={onModeChange} />
      </WindowTitlebar>
    ) : (
      <InnerBar disabled={disabled} mode={mode} justify_mode='center' onModeChange={onModeChange} />
    )
  );
}
