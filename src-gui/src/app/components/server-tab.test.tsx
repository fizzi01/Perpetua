import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';

import {ServerTab} from './server-tab';
import {
    ADVERTISE_AUTO,
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

vi.mock('../api/Listener', () => ({
    listenCommand: vi.fn((eventType: EventType, commandType: CommandType, callback: CommandCallback) => {
        commandListeners.set(`${eventType}:${commandType}`, callback);
        return Promise.resolve(() => commandListeners.delete(`${eventType}:${commandType}`));
    }),
    listenGeneralEvent: vi.fn((eventType: EventType, _noData: boolean, callback: GeneralCallback) => {
        generalListeners.set(eventType, callback);
        return Promise.resolve(() => generalListeners.delete(eventType));
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
        host: ADVERTISE_AUTO,
        host_exclusive: false,
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
    selected: ADVERTISE_AUTO,
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
    await waitFor(() => expect(screen.getByLabelText(/advertise on/i)).toBeInTheDocument());
}

beforeEach(() => {
    commandListeners.clear();
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

        const select = screen.getByRole('combobox', {name: /advertise on/i});

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

        fireEvent.keyDown(screen.getByRole('combobox', {name: /advertise on/i}), {key: 'Enter'});
        fireEvent.click(screen.getByRole('option', {name: /Ethernet 1/}));
        await act(async () => {
            vi.advanceTimersByTime(500);
        });

        expect(saveServerConfig).toHaveBeenCalledWith('10.0.0.1', 5555, true, false);
    });

    it('shows what clients will actually be told', async () => {
        await renderServerTab();
        fireInterfaces();
        await openOptions();

        expect(screen.queryByText(/Will advertise|Reachable at/)).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', {name: 'Addresses to advertise · 2'}));
        expect(screen.getByText('192.168.1.20:5555')).toBeInTheDocument();
        expect(screen.getByText('10.0.0.1:5555')).toBeInTheDocument();
        expect(screen.getByText('Wi-Fi')).toBeInTheDocument();
    });

    it('keeps a vanished selection visible instead of snapping to Auto', async () => {
        // Silently resetting would hide the real problem: cable unplugged.
        await renderServerTab(serverState({host: '172.16.9.9'}));
        fireInterfaces({...INTERFACES, selected: '172.16.9.9'});
        await openOptions();

        fireEvent.keyDown(screen.getByRole('combobox', {name: /advertise on/i}), {key: 'Enter'});
        expect(screen.getByRole('option', {name: /172\.16\.9\.9 \(not present\)/})).toBeInTheDocument();
        expect(screen.getByText(/not currently available/i)).toBeInTheDocument();
    });

    it('surfaces the one-time change-of-behaviour notice', async () => {
        await renderServerTab(serverState({host: '192.168.1.20'}));
        fireInterfaces({...INTERFACES, selected: '192.168.1.20', legacy_bind_notice: '192.168.1.20'});

        expect(await screen.findByText(/listens on all interfaces/i)).toBeInTheDocument();
    });
});

describe('Network addresses in Options', () => {
    it('does not duplicate addresses below the statistics', async () => {
        await renderServerTab();
        fireInterfaces();
        expect(screen.queryByText(/Will advertise|Reachable at/)).not.toBeInTheDocument();
        expect(screen.queryByRole('button', {name: /addresses to advertise/i})).not.toBeInTheDocument();
        expect(screen.queryByTitle(/copy 192\.168\.1\.20:5555/i)).not.toBeInTheDocument();
    });

    it('shows only advertised addresses and retains the exclusive option', async () => {
        await renderServerTab(serverState({running: true, host: '10.0.0.1', host_exclusive: true}));
        fireInterfaces({...INTERFACES, selected: '10.0.0.1', advertised: ['10.0.0.1']});
        await openOptions();
        expect(screen.getByLabelText(/Accept only on this interface/i)).toBeChecked();
        fireEvent.click(screen.getByRole('button', {name: 'Advertised addresses · 1'}));
        expect(screen.getByText('10.0.0.1:5555')).toBeInTheDocument();
        expect(screen.queryByText('192.168.1.20:5555')).not.toBeInTheDocument();
    });

    it('shows a quiet empty state when no address is usable', async () => {
        await renderServerTab();
        fireInterfaces({...INTERFACES, interfaces: [], advertised: []});
        await openOptions();
        expect(screen.getByText('No usable network address')).toBeInTheDocument();
        expect(screen.queryByRole('button', {name: /addresses to advertise/i})).not.toBeInTheDocument();
    });
});

describe('Advanced management stays usable while running', () => {
    it('lets the interface be changed without stopping the server', async () => {
        // The daemon re-issues the certificate SAN and re-announces on the
        // fly, so forcing a stop would be friction with nothing behind it.
        await renderServerTab(serverState({running: true}));
        fireInterfaces();
        await openOptions();

        expect(screen.getByLabelText(/advertise on/i)).not.toBeDisabled();
    });

    it('still requires a stop to change the listening port', async () => {
        await renderServerTab(serverState({running: true}));
        fireInterfaces();
        await openOptions();

        expect(screen.getByLabelText(/^port$/i)).toBeDisabled();
    });
});

describe('Isolation toggle', () => {
    it('is hidden while advertising on every interface', async () => {
        await renderServerTab();
        fireInterfaces();
        await openOptions();

        expect(screen.queryByLabelText(/accept only on this interface/i)).not.toBeInTheDocument();
    });

    it('appears once a single interface is chosen', async () => {
        await renderServerTab(serverState({host: '10.0.0.1'}));
        fireInterfaces({...INTERFACES, selected: '10.0.0.1'});
        await openOptions();

        expect(screen.getByLabelText(/accept only on this interface/i)).toBeInTheDocument();
    });
});

describe('Partial saves do not clobber other fields', () => {
    it('editing only the port keeps the advertise address', async () => {
        // The regression: the port handler shipped the `host` captured in its
        // closure, silently persisting an address the user never chose.
        await renderServerTab(serverState({host: '10.0.0.1'}));
        fireInterfaces({...INTERFACES, selected: '10.0.0.1'});
        await openOptions();

        fireEvent.change(screen.getByLabelText(/^port$/i), {target: {value: '6000'}});
        await act(async () => {
            vi.advanceTimersByTime(500);
        });

        expect(saveServerConfig).toHaveBeenCalledWith('10.0.0.1', 6000, true, false);
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
