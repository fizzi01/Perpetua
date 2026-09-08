import {StrictMode} from 'react';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';

import {ServerTab} from './server-tab';
import {startServer, stopServer, switchTrayIcon} from '../api/Sender';
import {
    BIND_ALL,
    CommandType,
    EventType,
    GeneralEvent,
    NetworkInterfacesResult,
    ServerStatus,
} from '../api/Interface';

type CommandCallback = (data: {data?: {command?: string; result?: unknown; error?: string}; message?: string}, command: CommandType) => void;
type GeneralCallback = (data: GeneralEvent) => void;

const commandListeners = new Map<string, CommandCallback>();
const generalListeners = new Map<EventType, GeneralCallback>();
const commandSubscriptions = new Map<string, Set<CommandCallback>>();
const generalSubscriptions = new Map<EventType, Set<GeneralCallback>>();
let deferLifecycleRegistration = false;
let deferRemoval = false;
let rejectLifecycleRegistration = false;
let finishRegistrations: (() => void)[] = [];
let finishRemovals: (() => void)[] = [];

// Assert the notification queue, independently of Motion's exiting DOM nodes.
vi.mock('./ui/inline-notification', () => ({
    InlineNotification: ({notifications}: {notifications: {id: string; message: string; description?: string}[]}) => (
        <div data-testid="notifications">{notifications.map(item => (
            <div key={item.id} data-notification-id={item.id}><p>{item.message}</p><p>{item.description}</p></div>
        ))}</div>
    ),
}));

vi.mock('../api/Listener', () => ({
    listenCommand: vi.fn((eventType: EventType, commandType: CommandType, callback: CommandCallback) => {
        const key = `${eventType}:${commandType}`;
        const lifecycle = commandType === CommandType.StartServer || commandType === CommandType.StopServer;
        if (lifecycle && rejectLifecycleRegistration) return Promise.reject(new Error('Registration failed'));
        const callbacks = commandSubscriptions.get(key) || new Set<CommandCallback>();
        callbacks.add(callback);
        commandSubscriptions.set(key, callbacks);
        commandListeners.set(key, (data, command) => [...callbacks].forEach(cb => cb(data, command)));
        const remove = () => { callbacks.delete(callback); };
        const unlisten = () => { if (deferRemoval) finishRemovals.push(remove); else remove(); };
        return lifecycle && deferLifecycleRegistration
            ? new Promise<() => void>(resolve => finishRegistrations.push(() => resolve(unlisten)))
            : Promise.resolve(unlisten);
    }),
    listenGeneralEvent: vi.fn((eventType: EventType, _noData: boolean, callback: GeneralCallback) => {
        const callbacks = generalSubscriptions.get(eventType) || new Set<GeneralCallback>();
        callbacks.add(callback);
        generalSubscriptions.set(eventType, callbacks);
        generalListeners.set(eventType, data => [...callbacks].forEach(cb => cb(data)));
        return Promise.resolve(() => { callbacks.delete(callback); });
    }),
}));

const saveServerConfig = vi.fn(() => Promise.resolve());
const listNetworkInterfaces = vi.fn(() => Promise.resolve());
const getLocalIpAddress = vi.fn(() => Promise.resolve('192.168.1.60'));

vi.mock('../api/Sender', () => ({
    addClient: vi.fn(() => Promise.resolve()),
    approveClient: vi.fn(() => Promise.resolve()),
    denyClient: vi.fn(() => Promise.resolve()),
    listNetworkInterfaces: (...a: unknown[]) => listNetworkInterfaces(...(a as [])),
    removeClient: vi.fn(() => Promise.resolve()),
    saveServerConfig: (...a: unknown[]) => saveServerConfig(...(a as [])),
    setClientLayout: vi.fn(() => Promise.resolve()),
    shareCertificate: vi.fn(() => Promise.resolve()),
    startServer: vi.fn(() => Promise.resolve()),
    stopServer: vi.fn(() => Promise.resolve()),
    switchTrayIcon: vi.fn(() => Promise.resolve()),
    getLocalIpAddress: (...a: unknown[]) => getLocalIpAddress(...(a as [])),
}));

function serverState(overrides: Partial<ServerStatus> = {}): ServerStatus {
    return {
        running: false,
        uid: 'server-uid',
        host: BIND_ALL,
        port: 5555,
        heartbeat_interval: 1,
        streams_enabled: {},
        ssl_enabled: true,
        authorized_clients: [],
        ...overrides,
    };
}

const INTERFACES: NetworkInterfacesResult = {
    interfaces: [
        {name: 'en0', display_name: 'Wi-Fi', ip: '192.168.1.20', prefix: 24, cidr: '192.168.1.0/24', is_default_route: true},
        {name: 'eth1', display_name: 'Ethernet 1', ip: '10.0.0.1', prefix: 24, cidr: '10.0.0.0/24', is_default_route: false},
    ],
    selected: BIND_ALL,
    advertised: ['192.168.1.20', '10.0.0.1'],
};

async function renderServerTab(state = serverState()) {
    const result = render(<ServerTab state={state} onStatusChange={vi.fn()}/>);
    await waitFor(() => expect(
        commandListeners.get(`${EventType.CommandSuccess}:${CommandType.ListNetworkInterfaces}`),
    ).toBeDefined());
    return result;
}

function fireInterfaces(result: NetworkInterfacesResult = INTERFACES) {
    act(() => {
        commandListeners.get(`${EventType.CommandSuccess}:${CommandType.ListNetworkInterfaces}`)?.({
            data: {command: 'list_network_interfaces', result},
            message: 'Network interfaces retrieved',
        }, CommandType.ListNetworkInterfaces);
    });
}

async function openOptions() {
    fireEvent.click(screen.getByText(/options/i));
    await waitFor(() => expect(screen.getByLabelText(/listen on/i)).toBeInTheDocument());
}

beforeEach(() => {
    commandListeners.clear();
    commandSubscriptions.clear();
    generalSubscriptions.clear();
    deferLifecycleRegistration = false;
    deferRemoval = false;
    rejectLifecycleRegistration = false;
    finishRegistrations = [];
    finishRemovals = [];
    vi.mocked(startServer).mockReset().mockResolvedValue(undefined);
    vi.mocked(switchTrayIcon).mockClear();
    vi.mocked(stopServer).mockReset().mockResolvedValue(undefined);
    generalListeners.clear();
    saveServerConfig.mockClear();
    listNetworkInterfaces.mockClear();
    getLocalIpAddress.mockClear();
    vi.useFakeTimers({shouldAdvanceTime: true});
});

afterEach(() => {
    vi.useRealTimers();
    cleanup();
});

describe('Server tab does not invent an address', () => {
    it('never asks the frontend for a local IP', async () => {
        // The old mount effect wrote the frontend's own route lookup into
        // `host`, which on a multi-homed machine is the wrong interface - and
        // the options save then persisted it as the bind address.
        await renderServerTab();

        expect(getLocalIpAddress).not.toHaveBeenCalled();
    });

    it('asks the daemon for the interface list instead', async () => {
        await renderServerTab();

        expect(listNetworkInterfaces).toHaveBeenCalled();
    });
});

describe('Advertise picker', () => {
    it('offers Auto plus one entry per interface', async () => {
        await renderServerTab();
        fireInterfaces();
        await openOptions();

        const select = screen.getByRole('combobox', {name: /listen on/i});

        expect(select).toHaveTextContent('Auto (all interfaces)');
        fireEvent.keyDown(select, {key: 'Enter'});
        expect(screen.getByRole('option', {name: /auto \(all interfaces\)/i})).toBeInTheDocument();
        expect(screen.getByRole('option', {name: /Wi-Fi — 192\.168\.1\.20\/24 \(default route\)/})).toBeInTheDocument();
        expect(screen.getByRole('option', {name: /Ethernet 1 — 10\.0\.0\.1\/24/})).toBeInTheDocument();
    });

    it('saves the chosen address', async () => {
        await renderServerTab();
        fireInterfaces();
        await openOptions();

        fireEvent.keyDown(screen.getByRole('combobox', {name: /listen on/i}), {key: 'Enter'});
        fireEvent.click(screen.getByRole('option', {name: /Ethernet 1/}));
        await act(async () => {
            vi.advanceTimersByTime(500);
        });

        expect(saveServerConfig).toHaveBeenCalledWith('10.0.0.1', 5555, true);
    });

    it('goes back to Auto', async () => {
        await renderServerTab(serverState({host: '10.0.0.1'}));
        fireInterfaces({...INTERFACES, selected: '10.0.0.1'});
        await openOptions();

        fireEvent.keyDown(screen.getByRole('combobox', {name: /listen on/i}), {key: 'Enter'});
        fireEvent.click(screen.getByRole('option', {name: /auto \(all interfaces\)/i}));
        await act(async () => {
            vi.advanceTimersByTime(500);
        });

        expect(saveServerConfig).toHaveBeenCalledWith(BIND_ALL, 5555, true);
    });

    it('shows what clients will actually be told', async () => {
        await renderServerTab();
        fireInterfaces();
        await openOptions();

        expect(screen.queryByText(/Will advertise|Reachable at/)).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', {name: 'Will be reachable at · 2'}));
        expect(screen.getByText('192.168.1.20:5555')).toBeInTheDocument();
        expect(screen.getByText('10.0.0.1:5555')).toBeInTheDocument();
        expect(screen.getByText('Wi-Fi')).toBeInTheDocument();
    });

    it('keeps a vanished selection visible instead of snapping to Auto', async () => {
        // Silently resetting would hide the real problem: the address the
        // server is about to bind is gone, and it will refuse to start.
        await renderServerTab(serverState({host: '172.16.9.9'}));
        fireInterfaces({...INTERFACES, selected: '172.16.9.9'});
        await openOptions();

        fireEvent.keyDown(screen.getByRole('combobox', {name: /listen on/i}), {key: 'Enter'});
        expect(screen.getByRole('option', {name: /172\.16\.9\.9 \(not present\)/})).toBeInTheDocument();
        expect(screen.getByText(/not on this machine right now/i)).toBeInTheDocument();
    });

});

describe('Network addresses in Options', () => {
    it('does not duplicate addresses below the statistics', async () => {
        await renderServerTab();
        fireInterfaces();
        expect(screen.queryByText(/Will advertise|Reachable at/)).not.toBeInTheDocument();
        expect(screen.queryByRole('button', {name: /reachable at/i})).not.toBeInTheDocument();
        expect(screen.queryByTitle(/copy 192\.168\.1\.20:5555/i)).not.toBeInTheDocument();
    });

    it('a bound address is the only one shown', async () => {
        // Isolation comes free from the bind: nothing else is listening, so
        // nothing else may be advertised.
        await renderServerTab(serverState({running: true, host: '10.0.0.1'}));
        fireInterfaces({...INTERFACES, selected: '10.0.0.1', advertised: ['10.0.0.1']});
        await openOptions();
        fireEvent.click(screen.getByRole('button', {name: 'Reachable at · 1'}));
        expect(screen.getByText('10.0.0.1:5555')).toBeInTheDocument();
        expect(screen.queryByText('192.168.1.20:5555')).not.toBeInTheDocument();
    });

    it('shows a quiet empty state when no address is usable', async () => {
        await renderServerTab();
        fireInterfaces({...INTERFACES, interfaces: [], advertised: []});
        await openOptions();
        expect(screen.getByText('No usable network address')).toBeInTheDocument();
        expect(screen.queryByRole('button', {name: /reachable at/i})).not.toBeInTheDocument();
    });
});

describe('Connection settings while running', () => {
    it('requires a stop to change the address', async () => {
        // The socket is already bound. Letting the picker move would show a
        // selection the running server is not honouring.
        await renderServerTab(serverState({running: true}));
        fireInterfaces();
        await openOptions();

        expect(screen.getByLabelText(/listen on/i)).toBeDisabled();
    });

    it('still requires a stop to change the listening port', async () => {
        await renderServerTab(serverState({running: true}));
        fireInterfaces();
        await openOptions();

        expect(screen.getByLabelText(/^port$/i)).toBeDisabled();
    });
});

describe('Partial saves do not clobber other fields', () => {
    it('editing only the port keeps the bind address', async () => {
        // The regression: the port handler shipped the `host` captured in its
        // closure, silently persisting an address the user never chose.
        await renderServerTab(serverState({host: '10.0.0.1'}));
        fireInterfaces({...INTERFACES, selected: '10.0.0.1'});
        await openOptions();

        fireEvent.change(screen.getByLabelText(/^port$/i), {target: {value: '6000'}});
        await act(async () => {
            vi.advanceTimersByTime(500);
        });

        expect(saveServerConfig).toHaveBeenCalledWith('10.0.0.1', 6000, true);
    });

    it('a status tick does not overwrite a port being typed', async () => {
        const {rerender} = await renderServerTab();
        fireInterfaces();
        await openOptions();

        const port = screen.getByLabelText(/^port$/i) as HTMLInputElement;
        port.focus();
        fireEvent.change(port, {target: {value: '6001'}});

        await act(async () => {
            rerender(<ServerTab state={serverState({port: 5555})} onStatusChange={vi.fn()}/>);
        });

        expect(port.value).toBe('6001');
    });
});


const startResult = (start_time = '2026-09-08T10:00:00.000Z') => ({host: '0.0.0.0', port: 55655, start_time});
function fireStart(result = startResult()) {
    act(() => commandListeners.get(`${EventType.CommandSuccess}:${CommandType.StartServer}`)?.({
        data: {result}, message: 'Server started successfully',
    }, CommandType.StartServer));
}

describe('Server lifecycle notifications', () => {
    it('handles a replayed start response once', async () => {
        await renderServerTab();
        fireStart();
        fireStart();
        expect(screen.getAllByText('Server started')).toHaveLength(1);
    });

    it('ignores StrictMode callbacks whose cleanup precedes registration completion', async () => {
        deferLifecycleRegistration = true;
        deferRemoval = true;
        render(<StrictMode><ServerTab state={serverState()} onStatusChange={vi.fn()}/></StrictMode>);
        await act(async () => finishRegistrations.splice(0).forEach(finish => finish()));
        expect(commandSubscriptions.get(`${EventType.CommandSuccess}:${CommandType.StartServer}`)?.size).toBe(2);
        fireStart();
        expect(screen.getAllByText('Server started')).toHaveLength(1);
    });
});


function powerButton() {
    return screen.getAllByRole('button')[0];
}
function fireStop() {
    act(() => commandListeners.get(`${EventType.CommandSuccess}:${CommandType.StopServer}`)?.({
        message: 'Server stopped',
    }, CommandType.StopServer));
}

describe('Server lifecycle regression scenarios', () => {
    it('waits for registrations and sends only one command on rapid clicks', async () => {
        deferLifecycleRegistration = true;
        await renderServerTab();
        expect(powerButton()).toBeDisabled();
        fireEvent.click(powerButton());
        expect(startServer).not.toHaveBeenCalled();
        await act(async () => finishRegistrations.splice(0).forEach(finish => finish()));
        expect(powerButton()).not.toBeDisabled();
        act(() => { powerButton().click(); powerButton().click(); });
        expect(startServer).toHaveBeenCalledTimes(1);
        fireStart();
        expect(screen.getAllByText('Server started')).toHaveLength(1);
    });

    it('reports registration failure and leaves controls disabled', async () => {
        rejectLifecycleRegistration = true;
        await renderServerTab();
        expect(await screen.findByText('Server controls unavailable')).toBeInTheDocument();
        expect(powerButton()).toBeDisabled();
        expect(startServer).not.toHaveBeenCalled();
    });

    it('accepts an immediate response and suppresses repeats without start_time', async () => {
        await renderServerTab();
        vi.mocked(startServer).mockImplementationOnce(async () => {
            commandListeners.get(`${EventType.CommandSuccess}:${CommandType.StartServer}`)?.({
                data: {result: {host: '0.0.0.0', port: 55655}},
            }, CommandType.StartServer);
        });
        fireEvent.click(powerButton());
        await act(async () => {});
        fireStart({host: '0.0.0.0', port: 55655, start_time: ''});
        expect(screen.getAllByText('Server started')).toHaveLength(1);
        expect(startServer).toHaveBeenCalledTimes(1);
    });

    it('announces a new session after stop and ignores old replays', async () => {
        await renderServerTab();
        fireEvent.click(powerButton());
        fireStart();
        fireEvent.click(powerButton());
        expect(stopServer).toHaveBeenCalledTimes(1);
        fireStop();
        fireStart();
        expect(screen.getByText('Server Stopped')).toBeInTheDocument();
        await act(async () => vi.advanceTimersByTime(4500));
        fireEvent.click(powerButton());
        fireStart(startResult('2026-09-08T10:01:00.000Z'));
        expect(screen.getAllByText('Server started')).toHaveLength(1);
        expect(startServer).toHaveBeenCalledTimes(2);
    });

    it('polling and remounting an active server do not replay the start notification', async () => {
        const state = serverState({running: true, start_time: startResult().start_time});
        const view = await renderServerTab(state);
        fireStart();
        expect(screen.queryByText('Server started')).not.toBeInTheDocument();
        const connectedCallbacks = [...(generalSubscriptions.get(EventType.ClientConnected) || [])];
        view.rerender(<ServerTab state={{...state, heartbeat_interval: 2}} onStatusChange={vi.fn()}/>);
        await act(async () => {});
        expect([...(generalSubscriptions.get(EventType.ClientConnected) || [])]).toEqual(connectedCallbacks);
        view.unmount();
        await renderServerTab(state);
        fireStart();
        expect(screen.queryByText('Server started')).not.toBeInTheDocument();
        expect(commandSubscriptions.get(`${EventType.CommandSuccess}:${CommandType.StartServer}`)?.size).toBe(1);
    });

    it('callbacks queued before unmount cannot touch the tray or create notifications', async () => {
        const view = await renderServerTab();
        const callback = [...commandSubscriptions.get(`${EventType.CommandSuccess}:${CommandType.StartServer}`)!][0];
        view.unmount();
        vi.mocked(switchTrayIcon).mockClear();
        act(() => callback({data: {result: startResult()}}, CommandType.StartServer));
        expect(switchTrayIcon).not.toHaveBeenCalled();
    });

    it('send failures release the operation so the user can retry', async () => {
        await renderServerTab();
        vi.mocked(startServer).mockRejectedValueOnce(new Error('IPC unavailable'));
        fireEvent.click(powerButton());
        expect(await screen.findByText('Failed to start server')).toBeInTheDocument();
        expect(powerButton()).not.toBeDisabled();
        fireEvent.click(powerButton());
        expect(startServer).toHaveBeenCalledTimes(2);
    });
});


it('gives notifications unique keys even when created in the same millisecond and clears their timers', async () => {
    const view = await renderServerTab();
    const now = vi.spyOn(Date, 'now').mockReturnValue(1788861600000);
    fireStart();
    fireStop();
    const entries = screen.getByTestId('notifications').querySelectorAll('[data-notification-id]');
    expect(entries).toHaveLength(2);
    expect(new Set([...entries].map(node => node.getAttribute('data-notification-id'))).size).toBe(2);
    view.unmount();
    await act(async () => vi.advanceTimersByTime(5000));
    now.mockRestore();
});
