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

/**
 * jsdom doesn't implement getComputedStyle with pseudoElt (used by
 * d3-selection / resize observers). Provide a stub.
 */
if (typeof window !== "undefined" && !window.getComputedStyle.toString().includes("pseudoElt")) {
  const origGetComputedStyle = window.getComputedStyle.bind(window)
  window.getComputedStyle = (elt: Element, pseudoElt?: string | null) => {
    if (pseudoElt) {
      return {} as CSSStyleDeclaration
    }
    return origGetComputedStyle(elt)
  }
}

/**
 * Node 22+ exposes an opt-in `globalThis.localStorage` only when started with
 * `--localstorage-file`; otherwise the property is `undefined`. Vitest's
 * jsdom environment was previously expected to attach its own storage to
 * `window.localStorage`, but on Node 22 / 26 the jsdom `Storage` shim is
 * not always installed before the user setup file runs, so any test that
 * touches `window.localStorage` (DataLossNotice, LiteratureManager drawer,
 * …) crashes inside `beforeEach`. Polyfill with an in-memory `Map`-backed
 * store that satisfies the `Storage` interface our code actually uses
 * (`getItem`, `setItem`, `removeItem`, `clear`, `length`, `key`).
 */
if (
  typeof window !== "undefined" &&
  typeof window.localStorage === "undefined"
) {
  const backing = new Map<string, string>()
  const makeStore = (): Storage => {
    const store: Storage = {
      get length(): number {
        return backing.size
      },
      clear(): void {
        backing.clear()
      },
      getItem(key: string): string | null {
        return backing.has(key) ? (backing.get(key) as string) : null
      },
      key(index: number): string | null {
        return Array.from(backing.keys())[index] ?? null
      },
      removeItem(key: string): void {
        backing.delete(key)
      },
      setItem(key: string, value: string): void {
        backing.set(key, String(value))
      },
    }
    return store
  }
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    writable: false,
    value: makeStore(),
  })
}
