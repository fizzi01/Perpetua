import {useEffect, useState} from 'react';
import {getCurrentWindow} from '@tauri-apps/api/window';
import {FileText, X} from 'lucide-react';
import {DaemonLogViewer} from './components/ui/DaemonLogViewer';

export default function LogsWindow() {
    const [active, setActive] = useState(false);
    const [error, setError] = useState('');
    useEffect(() => {
        const window = getCurrentWindow();
        let disposed = false;
        const unlisteners: (() => void)[] = [];
        const updateVisibility = async () => {
            const visible = await window.isVisible();
            if (!disposed) setActive(visible);
        };
        const register = async (promise: Promise<() => void>) => {
            const unlisten = await promise;
            if (disposed) unlisten();
            else unlisteners.push(unlisten);
        };
        Promise.all([
            register(window.onFocusChanged(() => { void updateVisibility().catch(console.error); })),
            register(window.onCloseRequested(() => { if (!disposed) setActive(false); })),
        ]).then(updateVisibility).catch(err => {
            if (!disposed) setError(String(err));
        });
        return () => {
            disposed = true;
            unlisteners.forEach(unlisten => unlisten());
        };
    }, []);

    const close = async () => {
        try {
            await getCurrentWindow().hide();
            setActive(false);
        } catch (err) { setError(String(err)); }
    };
    return (
        <div className="w-screen h-screen p-4 box-border flex flex-col gap-3"
             style={{backgroundColor: 'var(--app-bg-secondary)', color: 'var(--app-text-primary)'}}>
            <header data-tauri-drag-region className="flex items-center gap-3 select-none shrink-0">
                <FileText size={18}/>
                <h1 className="text-lg font-bold">Logs</h1>
                <div data-tauri-drag-region className="flex-1 self-stretch"/>
                <button type="button" onClick={() => void close()} aria-label="Close logs" title="Close logs"
                        className="p-2 rounded-lg border cursor-pointer hover:opacity-80"
                        style={{borderColor: 'var(--app-border)', backgroundColor: 'var(--app-bg-tertiary)'}}>
                    <X size={18}/>
                </button>
            </header>
            {error && <p role="alert" className="text-xs">{error}</p>}
            <div className="flex-1 min-h-0 overflow-hidden rounded-xl border" style={{borderColor: 'var(--app-border)'}}>
                <DaemonLogViewer active={active}/>
            </div>
        </div>
    );
}
