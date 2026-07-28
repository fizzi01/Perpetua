import '@testing-library/jest-dom/vitest';

// jsdom ships no ResizeObserver; the custom scrollbar observes its content.
if (!('ResizeObserver' in globalThis)) {
    class ResizeObserverStub {
        observe() {
        }

        unobserve() {
        }

        disconnect() {
        }
    }

    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
}
