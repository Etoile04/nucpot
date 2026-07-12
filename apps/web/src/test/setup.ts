import "@testing-library/jest-dom/vitest"

// jsdom lacks several browser APIs that Ant Design v5 relies on at render time.
// Provide minimal stubs so component tests render.  These are additive — they
// do not alter existing test behavior.
if (typeof window !== "undefined") {
  if (!window.matchMedia) {
    window.matchMedia = (query: string) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList
  }
  if (!window.ResizeObserver) {
    window.ResizeObserver = class ResizeObserver {
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      constructor(_: ResizeObserverCallback) {}
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver
  }
  if (!window.IntersectionObserver) {
    window.IntersectionObserver = class IntersectionObserver {
      readonly root: Element | null = null
      readonly rootMargin = ""
      readonly thresholds: ReadonlyArray<number> = [0]
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      constructor(_: IntersectionObserverCallback, __?: IntersectionObserverInit) {}
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords(): IntersectionObserverEntry[] {
        return []
      }
    } as unknown as typeof IntersectionObserver
  }
}
