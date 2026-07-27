import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';

import {ClientTab} from './client-tab';
import {ClientStatus, CommandType, EventType, GeneralEvent} from '../api/Interface';

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

vi.mock('../api/Sender', () => ({
    chooseServer: vi.fn(() => Promise.resolve()),
    getLocalIpAddress: vi.fn(() => Promise.resolve('127.0.0.1')),
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
