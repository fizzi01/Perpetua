import {act, cleanup, render, screen, waitFor} from '@testing-library/react';
import {Provider} from 'react-redux';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';

import {Main} from './App';
import {CommandType, EventType, GeneralEvent} from './api/Interface';
import {store} from './store/store';

type CommandCallback = (data: {data?: {command?: string; result?: unknown; error?: string}; message?: string}, command: CommandType) => void;
type GeneralCallback = (data: GeneralEvent) => void;

const commandListeners = new Map<string, CommandCallback>();
const generalListeners = new Map<EventType, GeneralCallback>();

vi.mock('./api/Listener', () => ({
    listenCommand: vi.fn((eventType: EventType, commandType: CommandType, callback: CommandCallback) => {
        commandListeners.set(`${eventType}:${commandType}`, callback);
        return Promise.resolve(() => commandListeners.delete(`${eventType}:${commandType}`));
    }),
    listenGeneralEvent: vi.fn((eventType: EventType, _noData: boolean, callback: GeneralCallback) => {
        generalListeners.set(eventType, callback);
        return Promise.resolve(() => generalListeners.delete(eventType));
    }),
}));

vi.mock('./api/Sender', () => ({
    chooseService: vi.fn(() => Promise.resolve()),
    getPermissions: vi.fn(() => Promise.resolve()),
    getStatus: vi.fn(() => Promise.resolve()),
}));

// The tabs pull in the whole Tauri surface; this suite only cares about the
// mode switch and the app-level error feedback.
vi.mock('./components/client-tab', () => ({
    ClientTab: () => <div data-testid="client-tab"/>,
}));
vi.mock('./components/server-tab', () => ({
    ServerTab: () => <div data-testid="server-tab"/>,
}));
vi.mock('./components/ui/DaemonLogDialog', () => ({
    DaemonLogDialog: () => null,
}));
// The real titlebar reaches for the Tauri OS plugin and native window controls.
vi.mock('./components/titlebar', () => ({
    Titlebar: ({disabled, onModeChange}: {
        disabled: boolean;
        mode: 'client' | 'server';
        onModeChange: (mode: 'client' | 'server') => void;
    }) => (
        <button data-testid="server-mode-button" disabled={disabled} onClick={() => onModeChange('server')}>
            SERVER
        </button>
    ),
}));

async function renderApp() {
    const result = render(<Provider store={store}><Main/></Provider>);
    // Both service_choice listeners are registered before the command is sent.
    await waitFor(() => {
        expect(commandListeners.get(`${EventType.CommandSuccess}:${CommandType.ServiceChoice}`)).toBeDefined();
        expect(commandListeners.get(`${EventType.CommandError}:${CommandType.ServiceChoice}`)).toBeDefined();
    });
    return result;
}

function fireServiceChoiceError(error: string) {
    act(() => {
        commandListeners.get(`${EventType.CommandError}:${CommandType.ServiceChoice}`)?.({
            data: {command: 'service_choice', error},
            message: `Command service_choice failed: ${error}`,
        }, CommandType.ServiceChoice);
    });
}

function fireServiceChoiceSuccess(mode: 'client' | 'server') {
    act(() => {
        commandListeners.get(`${EventType.CommandSuccess}:${CommandType.ServiceChoice}`)?.({
            data: {command: 'service_choice'},
            message: mode,
        }, CommandType.ServiceChoice);
    });
}

describe('Main service_choice handling', () => {
    beforeEach(() => {
        commandListeners.clear();
        generalListeners.clear();
        vi.clearAllMocks();
    });

    afterEach(() => {
        cleanup();
    });

    it('registers a command_error listener for service_choice', async () => {
        await renderApp();
        expect(commandListeners.get(`${EventType.CommandError}:${CommandType.ServiceChoice}`)).toBeDefined();
    });

    it('shows the daemon error and keeps the previous mode on failure', async () => {
        await renderApp();
        expect(screen.getByTestId('client-tab')).toBeInTheDocument();

        fireServiceChoiceError('Could not determine local IP address ([Errno 51] Network is unreachable)');

        expect(await screen.findByText(/Cannot switch to server mode|Cannot switch to client mode/)).toBeInTheDocument();
        expect(screen.getByText(/Network is unreachable/)).toBeInTheDocument();
        // The daemon did not switch, so neither does the UI.
        expect(screen.getByTestId('client-tab')).toBeInTheDocument();
        expect(screen.queryByTestId('server-tab')).not.toBeInTheDocument();
    });

    it('switches mode on success without showing an error', async () => {
        await renderApp();

        fireServiceChoiceSuccess('server');

        expect(await screen.findByTestId('server-tab')).toBeInTheDocument();
        expect(screen.queryByText(/Cannot switch to/)).not.toBeInTheDocument();
    });
});
