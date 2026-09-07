import {Window} from '@tauri-apps/api/window';

export async function openLogWindow() {
    const window = await Window.getByLabel('logs');
    if (!window) throw new Error('Logs window is unavailable. Restart Perpetua.');
    await window.show();
    await window.setFocus();
}
