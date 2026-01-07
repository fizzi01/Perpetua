import { motion } from 'motion/react';

interface TitlebarProps {
  mode: 'client' | 'server';
  onModeChange: (mode: 'client' | 'server') => void;
}

export function Titlebar({ mode, onModeChange }: TitlebarProps) {
  return (
    <div 
      data-tauri-drag-region
      className="titlebar w-full px-6 py-1.5 flex items-center justify-center border-b backdrop-blur-md"
    >
      {/* Mode Toggle */}
      <motion.div 
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.3 }}
        className="titlebar-toggle w-[200px] rounded-md p-0.5 flex shadow-sm border"
      >
        <button
          onClick={() => onModeChange('client')}
          className={`titlebar-button py-1.5 px-4 rounded-sm font-medium text-xs tracking-wide ${
            mode === 'client' ? 'active' : ''
          }`}
        >
          CLIENT
        </button>
        <button
          onClick={() => onModeChange('server')}
          className={`titlebar-button py-1.5 px-4 rounded-sm font-medium text-xs tracking-wide ${
            mode === 'server' ? 'active' : ''
          }`}
        >
          SERVER
        </button>
      </motion.div>
    </div>
  );
}
