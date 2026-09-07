import {StrictMode} from 'react';
import {act, cleanup, fireEvent, render, screen} from '@testing-library/react';
import {configureStore} from '@reduxjs/toolkit';
import {Provider} from 'react-redux';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {Main} from './App';
import {openLogWindow} from './api/logWindow';
import {ClientStatus, CommandType, EventType, ServerStatus} from './api/Interface';
import reducer from './store/reducer';
import {chooseService, getPermissions, getStatus} from './api/Sender';

type Callback = (event: any, command: CommandType) => void;
const listeners = new Map<string, Set<Callback>>();
let registrationBarrier: Promise<void>;

vi.mock('./api/Listener', () => ({
    listenCommand: vi.fn(async (event: EventType, command: CommandType, callback: Callback) => {
        await registrationBarrier;
        const key = `${event}:${command}`;
        const callbacks = listeners.get(key) || new Set();
        callbacks.add(callback);
        listeners.set(key, callbacks);
        return () => { callbacks.delete(callback); };
    }),
    listenGeneralEvent: vi.fn(async () => () => {}),
}));
vi.mock('./api/Sender', () => ({
    chooseService: vi.fn(() => Promise.resolve()),
    getPermissions: vi.fn(() => Promise.resolve()),
    getStatus: vi.fn(() => Promise.resolve()),
}));
vi.mock('./components/client-tab', () => ({
    ClientTab: ({state}: {state: ClientStatus}) => <div data-testid="client-tab">{state.client_hostname}</div>,
}));
vi.mock('./components/server-tab', () => ({
    ServerTab: ({state}: {state: ServerStatus}) => <div data-testid="server-tab">{state.uid}</div>,
}));
vi.mock('./api/logWindow', () => ({openLogWindow: vi.fn(() => Promise.resolve())}));
vi.mock('./components/ui/permission-gate', () => ({PermissionGate: () => <div data-testid="permission-gate"/>}));
vi.mock('./components/titlebar', () => ({
    Titlebar: ({disabled, onModeChange}: {disabled: boolean; onModeChange: (mode: 'client' | 'server') => void}) => (
        <>
            <button data-testid="server-mode-button" disabled={disabled} onClick={() => onModeChange('server')}>SERVER</button>
            <button data-testid="client-mode-button" disabled={disabled} onClick={() => onModeChange('client')}>CLIENT</button>
        </>
    ),
}));

function emit(command: CommandType, result?: unknown, error?: string, message = 'Success') {
    const event = error ? EventType.CommandError : EventType.CommandSuccess;
    listeners.get(`${event}:${command}`)?.forEach(callback => callback({data: {result, error}, message}, command));
}
async function reply(command: CommandType, result?: unknown, error?: string, message?: string) {
    await act(async () => { emit(command, result, error, message); });
}
function client(running = false) {
    return {...configureStore({reducer}).getState().client, running, client_hostname: 'Saved client', ssl_enabled: true};
}
function server(running = false) {
    return {...configureStore({reducer}).getState().server, running, uid: 'Saved server'};
}
async function renderApp(strict = false) {
    const store = configureStore({reducer});
    await act(async () => {
        render(<Provider store={store}>{strict ? <StrictMode><Main/></StrictMode> : <Main/>}</Provider>);
    });
    return store;
}
async function readyClient() {
    await renderApp();
    await reply(CommandType.Status, {});
    await reply(CommandType.ServiceChoice, undefined, undefined, 'client');
    await reply(CommandType.Status, {client_info: client()});
}

beforeEach(() => {
    listeners.clear();
    vi.clearAllMocks();
    registrationBarrier = Promise.resolve();
});
afterEach(() => {
    cleanup();
    vi.useRealTimers();
});

describe('daemon initialization', () => {
    it('waits for listener registration and service confirmation before hydration', async () => {
        let registered!: () => void;
        registrationBarrier = new Promise(resolve => { registered = resolve; });
        await renderApp();
        expect(getStatus).not.toHaveBeenCalled();
        expect(screen.getByRole('status')).toHaveTextContent('Loading configuration');
        expect(screen.queryByTestId('client-tab')).not.toBeInTheDocument();
        expect(screen.getByTestId('server-mode-button')).toBeDisabled();
        await act(async () => registered());
        expect(getStatus).toHaveBeenCalledTimes(1);
        expect(chooseService).not.toHaveBeenCalled();
        await reply(CommandType.Status, {});
        expect(chooseService).toHaveBeenCalledWith('client');
        expect(getStatus).toHaveBeenCalledTimes(1);
        await reply(CommandType.ServiceChoice, undefined, undefined, 'client');
        expect(getStatus).toHaveBeenCalledTimes(2);
        expect(screen.queryByTestId('client-tab')).not.toBeInTheDocument();
        await reply(CommandType.Status, {client_info: client()});
        expect(screen.getByTestId('client-tab')).toHaveTextContent('Saved client');
    });

    it.each(['client', 'server'] as const)('adopts an already running %s without choosing a service', async mode => {
        const store = await renderApp();
        await reply(CommandType.Status, mode === 'client' ? {client_info: client(true)} : {server_info: server(true)});
        expect(chooseService).not.toHaveBeenCalled();
        expect(screen.getByTestId(`${mode}-tab`)).toBeInTheDocument();
        expect(store.getState()[mode].running).toBe(true);
    });

    it('accepts immediate replies after registration', async () => {
        vi.mocked(getStatus).mockImplementationOnce(async () => { emit(CommandType.Status, {client_info: client(true)}); });
        await renderApp();
        expect(screen.getByTestId('client-tab')).toHaveTextContent('Saved client');
    });

    it('never considers a missing service section hydrated', async () => {
        await renderApp();
        await reply(CommandType.Status, {});
        await reply(CommandType.ServiceChoice, undefined, undefined, 'client');
        await reply(CommandType.Status, {});
        expect(screen.queryByTestId('client-tab')).not.toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent('selected service configuration');
        expect(screen.getByRole('button', {name: 'Retry'})).toBeInTheDocument();
    });

    it('recovers when a server starts during the initial choice', async () => {
        await renderApp();
        await reply(CommandType.Status, {});
        await reply(CommandType.ServiceChoice, undefined, 'Server is running');
        expect(getStatus).toHaveBeenCalledTimes(2);
        await reply(CommandType.Status, {server_info: server(true)});
        expect(screen.getByTestId('server-tab')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('times out without polling and can accept a late valid snapshot', async () => {
        vi.useFakeTimers();
        await renderApp();
        await act(async () => vi.advanceTimersByTime(10000));
        expect(getStatus).toHaveBeenCalledTimes(1);
        expect(screen.getByRole('button', {name: 'Retry'})).toBeInTheDocument();
        await reply(CommandType.Status, {client_info: client(true)});
        expect(screen.getByTestId('client-tab')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('retries by inspecting status before issuing another choice', async () => {
        await renderApp();
        await reply(CommandType.Status, undefined, 'Unavailable');
        fireEvent.click(screen.getByRole('button', {name: 'Retry'}));
        expect(getStatus).toHaveBeenCalledTimes(2);
        expect(chooseService).not.toHaveBeenCalled();
        await reply(CommandType.Status, {server_info: server(true)});
        expect(screen.getByTestId('server-tab')).toBeInTheDocument();
    });


    it('keeps permissions and logs accessible while configuration is loading', async () => {
        await renderApp();
        expect(getPermissions).toHaveBeenCalledTimes(1);
        await reply(CommandType.GetPermissions, {missing: [{permission_type: 'accessibility'}], pending_service: 'server'});
        expect(screen.getByTestId('permission-gate')).toBeInTheDocument();
        expect(screen.queryByTestId('client-tab')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', {name: 'Open logs'}));
        expect(openLogWindow).toHaveBeenCalledTimes(1);
    });

    it('shows a recoverable error when sending the initial query fails', async () => {
        vi.mocked(getStatus).mockRejectedValueOnce(new Error('IPC unavailable'));
        await renderApp();
        expect(screen.getByRole('alert')).toHaveTextContent('IPC unavailable');
        expect(screen.queryByTestId('client-tab')).not.toBeInTheDocument();
        expect(screen.getByRole('button', {name: 'Retry'})).toBeInTheDocument();
    });

    it('does not duplicate requests or leak listeners in StrictMode', async () => {
        await renderApp(true);
        expect(getStatus).toHaveBeenCalledTimes(1);
        expect([...listeners.values()].every(callbacks => callbacks.size === 1)).toBe(true);
        await reply(CommandType.Status, {client_info: client(true)});
        expect(screen.getByTestId('client-tab')).toBeInTheDocument();
        cleanup();
        expect([...listeners.values()].every(callbacks => callbacks.size === 0)).toBe(true);
    });
});

describe('mode changes and polling', () => {
    it('uses cached tabs immediately and refreshes after confirmation without skeletons', async () => {
        await readyClient();
        fireEvent.click(screen.getByTestId('server-mode-button'));
        await reply(CommandType.ServiceChoice, undefined, undefined, 'server');
        await reply(CommandType.Status, {server_info: server()});
        fireEvent.click(screen.getByTestId('client-mode-button'));
        expect(screen.getByTestId('client-tab')).toHaveTextContent('Saved client');
        expect(screen.queryByRole('status')).not.toBeInTheDocument();
        expect(screen.getByTestId('client-tab').closest('[inert]')).not.toBeNull();
        await reply(CommandType.ServiceChoice, undefined, undefined, 'client');
        expect(screen.getByTestId('client-tab').closest('[inert]')).toBeNull();
        expect(screen.queryByRole('status')).not.toBeInTheDocument();
        await reply(CommandType.Status, {client_info: {...client(), client_hostname: 'Updated client'}});
        expect(screen.getByTestId('client-tab')).toHaveTextContent('Updated client');
    });

    it('rolls back a cached tab if the daemon rejects the switch', async () => {
        await readyClient();
        fireEvent.click(screen.getByTestId('server-mode-button'));
        await reply(CommandType.ServiceChoice, undefined, undefined, 'server');
        await reply(CommandType.Status, {server_info: server()});
        fireEvent.click(screen.getByTestId('client-mode-button'));
        expect(screen.getByTestId('client-tab')).toBeInTheDocument();
        await reply(CommandType.ServiceChoice, undefined, 'Cannot switch');
        await reply(CommandType.Status, {server_info: server()});
        expect(screen.getByTestId('server-tab')).toBeInTheDocument();
        expect(screen.queryByTestId('client-tab')).not.toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent('Cannot switch');
    });

    it('shows a skeleton for an uncached tab until its configuration arrives', async () => {
        await readyClient();
        fireEvent.click(screen.getByTestId('server-mode-button'));
        expect(screen.queryByTestId('client-tab')).not.toBeInTheDocument();
        expect(screen.getByRole('status')).toHaveAccessibleName('Loading configuration');
        await reply(CommandType.ServiceChoice, undefined, undefined, 'server');
        expect(screen.queryByTestId('server-tab')).not.toBeInTheDocument();
        await reply(CommandType.Status, {server_info: server()});
        expect(screen.getByTestId('server-tab')).toHaveTextContent('Saved server');
        expect(screen.queryByTestId('client-tab')).not.toBeInTheDocument();
    });

    it('keeps the previous mode and reports choice failure after checking for an active service', async () => {
        await readyClient();
        fireEvent.click(screen.getByTestId('server-mode-button'));
        await reply(CommandType.ServiceChoice, undefined, 'Network is unreachable');
        await reply(CommandType.Status, {client_info: client()});
        expect(screen.getByTestId('client-tab')).toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent('Cannot switch to server mode: Network is unreachable');
        expect(screen.getByTestId('server-mode-button')).not.toBeDisabled();
    });

    it('serializes polling and queues a mode change behind a pending status', async () => {
        vi.useFakeTimers();
        await readyClient();
        await act(async () => vi.advanceTimersByTime(6000));
        expect(getStatus).toHaveBeenCalledTimes(3);
        fireEvent.click(screen.getByTestId('server-mode-button'));
        expect(chooseService).toHaveBeenCalledTimes(1);
        await reply(CommandType.Status, {client_info: client()});
        expect(chooseService).toHaveBeenLastCalledWith('server');
        await act(async () => vi.advanceTimersByTime(2000));
        expect(getStatus).toHaveBeenCalledTimes(3);
        await reply(CommandType.ServiceChoice, undefined, undefined, 'server');
        expect(getStatus).toHaveBeenCalledTimes(4);
    });
});
