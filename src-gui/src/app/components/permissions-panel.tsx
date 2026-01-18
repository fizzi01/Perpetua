import { motion } from 'motion/react';
import { MousePointer, Keyboard, Clipboard } from 'lucide-react';
import { StreamType } from '../api/Interface';
import { enableStream, disableStream } from '../api/Sender';
import { listenCommand } from '../api/Listener';
import { EventType, CommandType } from '../api/Interface';
import { useEventListeners } from '../hooks/useEventListeners';

interface PermissionsPanelProps {
  /** Mouse permission enabled state */
  enableMouse: boolean;
  /** Keyboard permission enabled state */
  enableKeyboard: boolean;
  /** Clipboard permission enabled state */
  enableClipboard: boolean;
  /** Mouse permission setter */
  setEnableMouse: React.Dispatch<React.SetStateAction<boolean>>;
  /** Keyboard permission setter */
  setEnableKeyboard: React.Dispatch<React.SetStateAction<boolean>>;
  /** Clipboard permission setter */
  setEnableClipboard: React.Dispatch<React.SetStateAction<boolean>>;
  /** Notification callback for errors */
  addNotification: (type: 'error' | 'success' | 'warning' | 'info', message: string, description?: string) => void;
  /** Event listeners manager */
  listeners: ReturnType<typeof useEventListeners>;
  /** Optional custom className */
  className?: string;
}

export function PermissionsPanel({
  enableMouse,
  enableKeyboard,
  enableClipboard,
  setEnableMouse,
  setEnableKeyboard,
  setEnableClipboard,
  addNotification,
  listeners,
  className = '',
}: PermissionsPanelProps) {

  const handleStreamToggle = (
    streamType: StreamType,
    enable: boolean,
    setState: React.Dispatch<React.SetStateAction<boolean>>
  ) => {
    if (enable) {
      listenCommand(EventType.CommandSuccess, CommandType.EnableStream, (event) => {
        console.log(`Stream enabled successfully: ${event.message}`);
        setState(true);
        listeners.removeListener('enable-stream-' + StreamType[streamType]);
      }).then((unlisten) => {
        listeners.addListener('enable-stream-' + StreamType[streamType], unlisten);
      });

      listenCommand(EventType.CommandError, CommandType.EnableStream, (event) => {
        addNotification('error', `Failed to enable ${StreamType[streamType]} stream`, event.data?.error || '');
        setState(false);
        listeners.removeListener('enable-stream-error-' + StreamType[streamType]);
      }).then((unlisten) => {
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
      }).then((unlisten) => {
        listeners.addListener('disable-stream-' + StreamType[streamType], unlisten);
      });

      listenCommand(EventType.CommandError, CommandType.DisableStream, (event) => {
        addNotification('error', `Failed to disable ${StreamType[streamType]} stream`, event.data?.error || '');
        setState(true);
        listeners.removeListener('disable-stream-error-' + StreamType[streamType]);
      }).then((unlisten) => {
        listeners.addListener('disable-stream-error-' + StreamType[streamType], unlisten);
      });

      disableStream(streamType).catch((err) => {
        console.error(`Error disabling ${StreamType[streamType]} stream:`, err);
        addNotification('error', `Failed to disable ${StreamType[streamType]} stream`);
        listeners.forceRemoveListener('disable-stream-' + StreamType[streamType]);
        listeners.forceRemoveListener('disable-stream-error-' + StreamType[streamType]);
      });
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay: 0.3 }}
      className={`p-4 rounded-lg border backdrop-blur-sm ${className}`}
      style={{
        backgroundColor: 'var(--app-card-bg)',
        borderColor: 'var(--app-card-border)',
      }}
    >
      <h4 className="text-sm font-semibold mb-2" style={{ color: 'var(--app-text-primary)' }}>
        Active Permissions
      </h4>
      <div className="flex gap-3">
        <motion.button
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          onClick={() => handleStreamToggle(StreamType.Mouse, !enableMouse, setEnableMouse)}
          className="flex items-center gap-2 px-3 py-2 rounded-lg transition-all cursor-pointer"
          style={{
            backgroundColor: enableMouse ? 'var(--app-success-bg)' : 'var(--app-danger-bg)',
            color: enableMouse ? 'var(--app-success)' : 'var(--app-danger)',
          }}
        >
          <MousePointer size={16} />
          <span className="text-xs font-semibold">Mouse</span>
        </motion.button>

        <motion.button
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          onClick={() => handleStreamToggle(StreamType.Keyboard, !enableKeyboard, setEnableKeyboard)}
          className="flex items-center gap-2 px-3 py-2 rounded-lg transition-all cursor-pointer"
          style={{
            backgroundColor: enableKeyboard ? 'var(--app-success-bg)' : 'var(--app-danger-bg)',
            color: enableKeyboard ? 'var(--app-success)' : 'var(--app-danger)',
          }}
        >
          <Keyboard size={16} />
          <span className="text-xs font-semibold">Keyboard</span>
        </motion.button>

        <motion.button
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          onClick={() => handleStreamToggle(StreamType.Clipboard, !enableClipboard, setEnableClipboard)}
          className="flex items-center gap-2 px-3 py-2 rounded-lg transition-all cursor-pointer"
          style={{
            backgroundColor: enableClipboard ? 'var(--app-success-bg)' : 'var(--app-danger-bg)',
            color: enableClipboard ? 'var(--app-success)' : 'var(--app-danger)',
          }}
        >
          <Clipboard size={16} />
          <span className="text-xs font-semibold">Clipboard</span>
        </motion.button>
      </div>
    </motion.div>
  );
}
