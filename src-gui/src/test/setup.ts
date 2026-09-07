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

// Radix Select scrolls the focused option into view; jsdom has no layout API.
if (!HTMLElement.prototype.scrollIntoView) {
    HTMLElement.prototype.scrollIntoView = () => {};
}
