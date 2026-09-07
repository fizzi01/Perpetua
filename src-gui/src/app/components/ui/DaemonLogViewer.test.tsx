import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {invoke} from '@tauri-apps/api/core';
import {platform} from '@tauri-apps/plugin-os';
import {DaemonLogViewer} from './DaemonLogViewer';

vi.mock('@tauri-apps/plugin-os', () => ({platform: vi.fn(() => 'linux')}));
vi.mock('@tauri-apps/api/core', () => ({invoke: vi.fn()}));
const writeText = vi.fn();
const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
const response = {logs: ['first entry', 'second entry'], total_lines: 2, log_file: '/tmp/daemon.log'};
beforeEach(() => {
    vi.mocked(platform).mockReturnValue('linux');
    vi.mocked(invoke).mockReset().mockResolvedValue(response);
    writeText.mockReset().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText}});
});
afterEach(() => {
    cleanup();
    if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard);
    else Reflect.deleteProperty(navigator, 'clipboard');
});
async function setup() {
    render(<DaemonLogViewer/>);
    await screen.findByText('first entry');
}
function menu() {
    fireEvent.keyDown(screen.getByRole('button', {name: 'Copy all'}), {key: 'Enter'});
}

describe('log actions', () => {
    it.each(['displayed', 'entire'])('uses the native macOS clipboard for %s logs', async scope => {
        vi.mocked(platform).mockReturnValue('macos');
        writeText.mockRejectedValue(new DOMException('Not allowed', 'NotAllowedError'));
        await setup();
        menu();
        fireEvent.click(screen.getByRole('menuitem', {name: scope === 'entire' ? 'Copy entire log' : 'Copy displayed lines (2)'}));
        await waitFor(() => expect(invoke).toHaveBeenCalledWith('copy_log_text', {text: 'first entry\nsecond entry'}));
        expect(writeText).not.toHaveBeenCalled();
        expect(await screen.findByRole('status')).toHaveTextContent('Copied to clipboard');
    });

    it('reports native clipboard failures without claiming success', async () => {
        vi.mocked(platform).mockReturnValue('macos');
        await setup();
        vi.mocked(invoke).mockRejectedValueOnce('Pasteboard unavailable');
        menu();
        fireEvent.click(screen.getByRole('menuitem', {name: 'Copy displayed lines (2)'}));
        expect(await screen.findByRole('alert')).toHaveTextContent('Pasteboard unavailable');
        expect(screen.queryByRole('status')).not.toBeInTheDocument();
        expect(writeText).not.toHaveBeenCalled();
    });

    it('pauses reads while hidden and refreshes on reopen without losing search', async () => {
        vi.useFakeTimers();
        try {
            const {rerender} = render(<DaemonLogViewer active={false}/>);
            await act(async () => vi.advanceTimersByTime(10000));
            expect(invoke).not.toHaveBeenCalled();
            await act(async () => rerender(<DaemonLogViewer active/>));
            expect(invoke).toHaveBeenCalledTimes(1);
            fireEvent.change(screen.getByPlaceholderText('Search logs...'), {target: {value: 'second'}});
            rerender(<DaemonLogViewer active={false}/>);
            await act(async () => vi.advanceTimersByTime(10000));
            expect(invoke).toHaveBeenCalledTimes(1);
            await act(async () => rerender(<DaemonLogViewer active/>));
            expect(invoke).toHaveBeenCalledTimes(2);
            expect(screen.getByPlaceholderText('Search logs...')).toHaveValue('second');
        } finally { vi.useRealTimers(); }
    });

    it('copies displayed lines in their original text and respects the search', async () => {
        await setup();
        fireEvent.change(screen.getByPlaceholderText('Search logs...'), {target: {value: 'second'}});
        menu();
        fireEvent.click(screen.getByRole('menuitem', {name: 'Copy displayed lines (1)'}));
        await waitFor(() => expect(writeText).toHaveBeenCalledWith('second entry'));
        expect(await screen.findByRole('status')).toHaveTextContent('Copied to clipboard');
        expect(invoke).toHaveBeenCalledTimes(1);
    });

    it('reads the entire file for full copy without replacing the visible tail', async () => {
        await setup();
        vi.mocked(invoke).mockResolvedValueOnce({...response, logs: ['older entry', ...response.logs]});
        fireEvent.change(screen.getByPlaceholderText('Search logs...'), {target: {value: 'second'}});
        menu();
        fireEvent.click(screen.getByRole('menuitem', {name: 'Copy entire log'}));
        await waitFor(() => expect(writeText).toHaveBeenCalledWith('older entry\nfirst entry\nsecond entry'));
        expect(invoke).toHaveBeenLastCalledWith('read_daemon_logs', {numLines: 0, all: true});
        expect(screen.queryByText('older entry')).not.toBeInTheDocument();
        expect(screen.getByPlaceholderText('Search logs...')).toHaveValue('second');
    });

    it('opens the backend-resolved file and reports failures', async () => {
        await setup();
        vi.mocked(invoke).mockRejectedValueOnce('No associated application');
        fireEvent.click(screen.getByRole('button', {name: 'Open log file'}));
        expect(invoke).toHaveBeenLastCalledWith('open_daemon_log');
        expect(await screen.findByRole('alert')).toHaveTextContent('Could not open log file: No associated application');
    });

    it('waits for the clipboard and reports a rejected copy', async () => {
        await setup();
        let reject!: (error: Error) => void;
        writeText.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
        menu();
        fireEvent.click(screen.getByRole('menuitem', {name: 'Copy displayed lines (2)'}));
        expect(screen.queryByRole('status')).not.toBeInTheDocument();
        expect(screen.getByRole('button', {name: 'Copy all'})).toBeDisabled();
        await act(async () => reject(new Error('Clipboard denied')));
        expect(await screen.findByRole('alert')).toHaveTextContent('Could not copy logs');
        expect(screen.getByRole('button', {name: 'Copy all'})).not.toBeDisabled();
    });

    it('disables displayed copy when the search has no matches', async () => {
        await setup();
        fireEvent.change(screen.getByPlaceholderText('Search logs...'), {target: {value: 'zzzz'}});
        menu();
        expect(screen.getByRole('menuitem', {name: 'Copy displayed lines (0)'})).toHaveAttribute('data-disabled');
        expect(screen.getByRole('menuitem', {name: 'Copy entire log'})).not.toHaveAttribute('data-disabled');
    });
});
