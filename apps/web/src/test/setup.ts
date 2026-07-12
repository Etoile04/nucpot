import "@testing-library/jest-dom/vitest"

/**
 * AntD v5 components that observe the viewport (Descriptions responsive
 * column, Grid Row/Col) call window.matchMedia during render. jsdom does not
 * implement matchMedia, so a missing mock throws inside rc-util's
 * useLayoutEffect and aborts the render. Provide a noop polyfill so those
 * components mount in tests.
 */
if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  window.matchMedia = (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  })
}
