import "@testing-library/jest-dom/vitest"

/**
 * Vitest 5's native config loader does not honor the `define` block in
 * vitest.config.ts, so `process.env.NODE_ENV` remains "production" in the
 * CI/deploy shell and React 19 selects its production build — which exports
 * `act` as a no-op stub and breaks `@testing-library/react`. Pin NODE_ENV
 * here, before any React import resolves, so the development build is
 * loaded and `act()` is real (NFM-4456).
 */
;(process.env as Record<string, string>).NODE_ENV = "development"

/**
 * React 19 requires consumers to opt-in to `act()` semantics via the
 * `IS_REACT_ACT_ENVIRONMENT` flag. Without this, `@testing-library/react`
 * throws `TypeError: React.act is not a function` on every renderHook and
 * render call (NFM-4456 surface — also blocks the pre-existing
 * `use-ontology-hooks.test.tsx`).
 */
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

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
 * jsdom doesn't implement ResizeObserver. Components that observe container
 * size (e.g. GraphCanvas at line 261) crash inside useEffect on mount.
 * Install a no-op stub that satisfies the ResizeObserver interface used in
 * the codebase (observe / unobserve / disconnect). Existing test files that
 * defined their own mock continue to work — they guard with
 * `if (!("ResizeObserver" in window))` and become no-ops once this stub is
 * in place.
 */
if (typeof window !== "undefined" && !("ResizeObserver" in window)) {
  class MockResizeObserver {
    constructor(_callback: ResizeObserverCallback) {}
    observe(_target: Element): void {}
    unobserve(_target: Element): void {}
    disconnect(): void {}
  }
  ;(window as unknown as { ResizeObserver: typeof ResizeObserver }).ResizeObserver =
    MockResizeObserver as unknown as typeof ResizeObserver
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
if (typeof window !== "undefined" && typeof window.localStorage === "undefined") {
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
