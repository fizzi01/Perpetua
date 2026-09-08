import {StrictMode} from 'react';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';

import {ClientTab} from './client-tab';
import {startClient, stopClient, switchTrayIcon} from '../api/Sender';
import {ClientStatus, CommandType, EventType, GeneralEvent} from '../api/Interface';

type CommandCallback = (data: {data?: {command?: string; result?: unknown; error?: string}; message?: string}, command: CommandType) => void;
type GeneralCallback = (data: GeneralEvent) => void;

const commandListeners = new Map<string, CommandCallback>();
const generalListeners = new Map<EventType, GeneralCallback>();
const commandSubscriptions = new Map<string, Set<CommandCallback>>();
const generalSubscriptions = new Map<EventType, Set<GeneralCallback>>();
let deferRegistration = false;
let deferRemoval = false;
let rejectRegistration = false;
let registrations: (() => void)[] = [];
let removals: (() => void)[] = [];

vi.mock('./ui/inline-notification', () => ({
    InlineNotification: ({notifications}: {notifications: {id: string; message: string; description?: string}[]}) => (
        <div data-testid="notifications">{notifications.map(item => (
            <div key={item.id} data-notification-id={item.id}><p>{item.message}</p><p>{item.description}</p></div>
        ))}</div>
    ),
}));

vi.mock('../api/Listener', () => ({
    listenCommand: vi.fn((eventType: EventType, commandType: CommandType, callback: CommandCallback) => {
        const lifecycle = commandType === CommandType.StartClient || commandType === CommandType.StopClient;
        if (lifecycle && rejectRegistration) return Promise.reject(new Error('Registration failed'));
        const key = `${eventType}:${commandType}`;
        const callbacks = commandSubscriptions.get(key) || new Set<CommandCallback>();
        callbacks.add(callback);
        commandSubscriptions.set(key, callbacks);
        commandListeners.set(key, (data, command) => [...callbacks].forEach(cb => cb(data, command)));
        const remove = () => { callbacks.delete(callback); };
        const unlisten = () => { if (deferRemoval) removals.push(remove); else remove(); };
        return lifecycle && deferRegistration ? new Promise<() => void>(resolve => registrations.push(() => resolve(unlisten))) : Promise.resolve(unlisten);
    }),
    listenGeneralEvent: vi.fn((eventType: EventType, _noData: boolean, callback: GeneralCallback) => {
        const callbacks = generalSubscriptions.get(eventType) || new Set<GeneralCallback>();
        callbacks.add(callback);
        generalSubscriptions.set(eventType, callbacks);
        generalListeners.set(eventType, data => [...callbacks].forEach(cb => cb(data)));
        const remove = () => { callbacks.delete(callback); };
        const unlisten = () => { if (deferRemoval) removals.push(remove); else remove(); };
        return deferRegistration ? new Promise<() => void>(resolve => registrations.push(() => resolve(unlisten))) : Promise.resolve(unlisten);
    }),
}));

vi.mock('../api/Sender', () => ({
    chooseServer: vi.fn(() => Promise.resolve()),
    getLocalIpAddress: vi.fn(() => Promise.resolve('127.0.0.1')),
    // The tab now asks the daemon for every local address instead of the
    // frontend's single route-probed one.
    listNetworkInterfaces: vi.fn(() => Promise.resolve()),
    saveClientConfig: vi.fn(() => Promise.resolve()),
    setOtp: vi.fn(() => Promise.resolve()),
    startClient: vi.fn(() => Promise.resolve()),
    stopClient: vi.fn(() => Promise.resolve()),
    switchTrayIcon: vi.fn(() => Promise.resolve()),
}));

function clientState(overrides: Partial<ClientStatus> = {}): ClientStatus {
    const baseServerInfo = {
        uid: 'server-1',
        host: '192.168.1.10',
        hostname: 'server-host',
        port: 8080,
        ssl: true,
        auto_reconnect: false,
    };

    return {
        running: true,
        connected: false,
        start_time: '2026-07-27T10:00:00.000Z',
        otp_needed: false,
        service_choice_needed: false,
        available_servers: [],
        uid: 'client-1',
        client_hostname: 'client-host',
        streams_enabled: [],
        ssl_enabled: true,
        ...overrides,
        server_info: {...baseServerInfo, ...overrides.server_info},
    };
}

async function renderClientTab(state = clientState()) {
    const result = render(<ClientTab state={state} onStatusChange={vi.fn()}/>);
    await waitFor(() => expect(generalListeners.get(EventType.OtpNeeded)).toBeDefined());
    return result;
}

function fireOtpNeeded() {
    act(() => {
        generalListeners.get(EventType.OtpNeeded)?.({
            data: {needed: true},
            message: 'OTP required for authentication',
        });
    });
}

function fireSetOtpSuccess() {
    act(() => {
        commandListeners.get(`${EventType.CommandSuccess}:${CommandType.SetOtp}`)?.({
            data: {command: 'set_otp'},
            message: 'OTP set successfully',
        }, CommandType.SetOtp);
    });
}

function fireSetOtpError() {
    act(() => {
        commandListeners.get(`${EventType.CommandError}:${CommandType.SetOtp}`)?.({
            data: {command: 'set_otp', error: 'Invalid OTP'},
            message: 'Failed to set OTP',
        }, CommandType.SetOtp);
    });
}

async function submitOtp() {
    fireEvent.change(screen.getByPlaceholderText('000000'), {target: {value: '123456'}});
    fireEvent.click(screen.getByRole('button', {name: /connect/i}));
    await waitFor(() => expect(commandListeners.get(`${EventType.CommandSuccess}:${CommandType.SetOtp}`)).toBeDefined());
}

afterEach(() => {
    cleanup();
    vi.clearAllMocks();
});

beforeEach(() => {
    commandSubscriptions.clear();
    generalSubscriptions.clear();
    deferRegistration = false;
    deferRemoval = false;
    rejectRegistration = false;
    registrations = [];
    removals = [];
    vi.mocked(startClient).mockReset().mockResolvedValue(undefined);
    vi.mocked(stopClient).mockReset().mockResolvedValue(undefined);
    commandListeners.clear();
    generalListeners.clear();
});

describe('ClientTab OTP pairing panel', () => {
    it('shows the OTP panel when OtpNeeded fires and no OTP has been submitted', async () => {
        await renderClientTab();

        fireOtpNeeded();

        expect(screen.getByText('Authentication Required')).toBeInTheDocument();
    });

    it('keeps the OTP panel hidden after SetOtp success for repeated status on the same pairing', async () => {
        const {rerender} = await renderClientTab(clientState({otp_needed: true}));
        await submitOtp();

        fireSetOtpSuccess();
        rerender(<ClientTab state={clientState({otp_needed: true})} onStatusChange={vi.fn()}/>);

        expect(screen.queryByText('Authentication Required')).not.toBeInTheDocument();
    });

    it('shows OTP again for a new pairing cycle with a different start time', async () => {
        const {rerender} = await renderClientTab(clientState({otp_needed: true}));
        await submitOtp();
        fireSetOtpSuccess();

        rerender(<ClientTab state={clientState({
            start_time: '2026-07-27T10:00:10.000Z',
            otp_needed: true,
        })} onStatusChange={vi.fn()}/>);

        expect(screen.getByText('Authentication Required')).toBeInTheDocument();
    });

    it('shows OTP again after a rejected submission', async () => {
        const {rerender} = await renderClientTab(clientState({otp_needed: true}));
        await submitOtp();

        fireSetOtpError();
        rerender(<ClientTab state={clientState({running: false, otp_needed: false})} onStatusChange={vi.fn()}/>);
        rerender(<ClientTab state={clientState({otp_needed: true})} onStatusChange={vi.fn()}/>);

        expect(screen.getByText('Authentication Required')).toBeInTheDocument();
    });

    it('shows OTP again after cancellation', async () => {
        const {rerender} = await renderClientTab(clientState({otp_needed: true}));

        fireEvent.click(screen.getByRole('button', {name: /cancel/i}));
        rerender(<ClientTab state={clientState({running: false, otp_needed: false})} onStatusChange={vi.fn()}/>);
        rerender(<ClientTab state={clientState({otp_needed: true})} onStatusChange={vi.fn()}/>);

        expect(screen.getByText('Authentication Required')).toBeInTheDocument();
    });
});


describe('local network addresses', () => {
    it('replaces IP chips with one control and retains interface names', async () => {
        await renderClientTab();
        await waitFor(() => expect(commandListeners.get(`${EventType.CommandSuccess}:${CommandType.ListNetworkInterfaces}`)).toBeDefined());
        act(() => {
            commandListeners.get(`${EventType.CommandSuccess}:${CommandType.ListNetworkInterfaces}`)?.({
                data: {result: {interfaces: [
                    {ip: '192.168.1.20', display_name: 'Wi-Fi'},
                    {ip: '10.0.0.1', display_name: 'Ethernet'},
                    {ip: '10.0.0.1', display_name: 'Duplicate'},
                ]}},
            }, CommandType.ListNetworkInterfaces);
        });
        expect(screen.queryByRole('button', {name: 'Copy 192.168.1.20'})).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', {name: 'Network addresses · 2'}));
        expect(screen.getByText('Wi-Fi')).toBeInTheDocument();
        expect(screen.getByText('Ethernet')).toBeInTheDocument();
        expect(screen.getByRole('button', {name: 'Copy 192.168.1.20'})).toBeInTheDocument();
        expect(screen.getByRole('button', {name: 'Copy 10.0.0.1'})).toBeInTheDocument();
    });
});


const stoppedClient = () => clientState({running: false, start_time: undefined});
const startResult = (start_time = '2026-09-08T10:00:00.000Z') => ({host: '192.168.1.10', port: 8080, start_time, enabled_streams: []});
function lifecycleReply(command: CommandType, result?: unknown, error?: string) {
    act(() => commandListeners.get(`${error ? EventType.CommandError : EventType.CommandSuccess}:${command}`)?.({
        data: {result, error}, message: 'Client lifecycle response',
    }, command));
}
function powerButton() { return screen.getAllByRole('button')[0]; }

describe('Client lifecycle regression scenarios', () => {
    it('handles an immediate success once and accepts another start after stop', async () => {
        await renderClientTab(stoppedClient());
        vi.mocked(startClient).mockImplementationOnce(async () => {
            commandListeners.get(`${EventType.CommandSuccess}:${CommandType.StartClient}`)?.({data: {result: startResult()}}, CommandType.StartClient);
        });
        fireEvent.click(powerButton());
        await act(async () => {});
        lifecycleReply(CommandType.StartClient, startResult());
        expect(screen.getAllByText('Started')).toHaveLength(1);
        fireEvent.click(screen.getByTitle('Force Stop'));
        lifecycleReply(CommandType.StopClient);
        lifecycleReply(CommandType.StopClient);
        expect(screen.getAllByText('Stopped')).toHaveLength(1);
        fireEvent.click(powerButton());
        lifecycleReply(CommandType.StartClient, startResult('2026-09-08T10:01:00.000Z'));
        expect(screen.getAllByText('Started')).toHaveLength(2);
    });

    it('waits for registrations and blocks rapid starts', async () => {
        deferRegistration = true;
        await renderClientTab(stoppedClient());
        expect(powerButton()).toBeDisabled();
        fireEvent.click(powerButton());
        expect(startClient).not.toHaveBeenCalled();
        await act(async () => registrations.splice(0).forEach(finish => finish()));
        act(() => { powerButton().click(); powerButton().click(); });
        expect(startClient).toHaveBeenCalledTimes(1);
    });

    it('ignores callbacks from the cleaned-up StrictMode registration', async () => {
        deferRegistration = true;
        deferRemoval = true;
        render(<StrictMode><ClientTab state={stoppedClient()} onStatusChange={vi.fn()}/></StrictMode>);
        await act(async () => registrations.splice(0).forEach(finish => finish()));
        expect(commandSubscriptions.get(`${EventType.CommandSuccess}:${CommandType.StartClient}`)?.size).toBe(2);
        lifecycleReply(CommandType.StartClient, startResult());
        expect(screen.getAllByText('Started')).toHaveLength(1);
    });

    it('allows one force stop during start and ignores a late start success', async () => {
        await renderClientTab(stoppedClient());
        fireEvent.click(powerButton());
        const stop = screen.getByTitle('Force Stop');
        act(() => { stop.click(); stop.click(); });
        expect(stopClient).toHaveBeenCalledTimes(1);
        lifecycleReply(CommandType.StartClient, startResult());
        expect(screen.queryByText('Started')).not.toBeInTheDocument();
        lifecycleReply(CommandType.StopClient);
        lifecycleReply(CommandType.StartClient, startResult());
        expect(screen.queryByText('Started')).not.toBeInTheDocument();
        expect(powerButton()).not.toBeDisabled();
    });

    it('uses operation identity when start_time is missing', async () => {
        await renderClientTab(stoppedClient());
        fireEvent.click(powerButton());
        lifecycleReply(CommandType.StartClient, startResult(''));
        lifecycleReply(CommandType.StartClient, startResult(''));
        expect(screen.getAllByText('Started')).toHaveLength(1);
    });

    it('keeps connection listeners stable through polling and deduplicates transitions', async () => {
        const view = await renderClientTab();
        const callbacks = [...generalSubscriptions.get(EventType.Connected)!];
        const connected = () => act(() => generalListeners.get(EventType.Connected)?.({data: startResult()}));
        connected();
        connected();
        expect(screen.getByTestId('notifications').textContent?.match(/Connected/g)).toHaveLength(1);
        view.rerender(<ClientTab state={clientState({connected: true})} onStatusChange={vi.fn()}/>);
        expect([...generalSubscriptions.get(EventType.Connected)!]).toEqual(callbacks);
        act(() => generalListeners.get(EventType.Disconnected)?.({data: {}}));
        act(() => generalListeners.get(EventType.Disconnected)?.({data: {}}));
        connected();
        expect(screen.getByTestId('notifications').textContent?.match(/Connected/g)).toHaveLength(2);
    });

    it('does not announce a hydrated session and ignores callbacks after unmount', async () => {
        const state = clientState();
        const view = await renderClientTab(state);
        lifecycleReply(CommandType.StartClient, startResult(state.start_time));
        expect(screen.queryByText('Started')).not.toBeInTheDocument();
        const callback = [...commandSubscriptions.get(`${EventType.CommandSuccess}:${CommandType.StartClient}`)!][0];
        view.unmount();
        vi.mocked(switchTrayIcon).mockClear();
        act(() => callback({data: {result: startResult()}}, CommandType.StartClient));
        expect(switchTrayIcon).not.toHaveBeenCalled();
    });

    it('keeps controls disabled when listener registration fails', async () => {
        rejectRegistration = true;
        await renderClientTab(stoppedClient());
        expect(await screen.findByText('Client controls unavailable')).toBeInTheDocument();
        expect(powerButton()).toBeDisabled();
    });

    it('releases pending flags after failed stop so it can be retried', async () => {
        await renderClientTab();
        fireEvent.click(screen.getByTitle('Force Stop'));
        lifecycleReply(CommandType.StopClient, undefined, 'Cannot stop');
        expect(await screen.findByText('Failed to Stop')).toBeInTheDocument();
        fireEvent.click(screen.getByTitle('Force Stop'));
        expect(stopClient).toHaveBeenCalledTimes(2);
    });
});
