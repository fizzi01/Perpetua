import {beforeEach, expect, it, vi} from 'vitest';
import {Window} from '@tauri-apps/api/window';
import {openLogWindow} from './logWindow';
vi.mock('@tauri-apps/api/window', () => ({Window: {getByLabel: vi.fn()}}));
beforeEach(() => vi.clearAllMocks());
it('shows and focuses the existing logs window', async () => {
    const show = vi.fn().mockResolvedValue(undefined);
    const setFocus = vi.fn().mockResolvedValue(undefined);
    vi.mocked(Window.getByLabel).mockResolvedValue({show, setFocus} as unknown as Window);
    await openLogWindow();
    expect(Window.getByLabel).toHaveBeenCalledWith('logs');
    expect(show).toHaveBeenCalledTimes(1);
    expect(setFocus).toHaveBeenCalledTimes(1);
});
it('reports when a restart is needed to create the native window', async () => {
    vi.mocked(Window.getByLabel).mockResolvedValue(null);
    await expect(openLogWindow()).rejects.toThrow('Restart Perpetua');
});
