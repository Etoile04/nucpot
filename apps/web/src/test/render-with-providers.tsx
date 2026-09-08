/**
 * Test render helper — wraps `render` with the providers the app
 * actually mounts at runtime.
 *
 * Why a helper instead of `setup.ts` wrapping globally:
 *   - Each test gets a fresh `QueryClient` so cache state never leaks
 *     between tests (TanStack Query caches by reference equality).
 *   - Adding more providers later (theme, auth, i18n) happens in ONE
 *     place, not in every test file.
 *
 * Import in tests as:
 *   import { render } from "@/test/render-with-providers"
 *
 * Existing imports of `render` from "@testing-library/react" need to
 * be replaced when a component uses `useQuery` (NFM-4455) — anything
 * that mounts a hook with TanStack Query backing.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render as rtlRender, type RenderOptions } from "@testing-library/react"
import type React from "react"

interface ProvidersOptions {
  /** Override the default test QueryClient. Defaults to a fresh client
   *  per `render()` call with `retry: false` and `gcTime: 0` so failed
   *  assertions don't get masked by background retries and unmounted
   *  components don't hold cached data. */
  readonly client?: QueryClient
}

export function render(
  ui: React.ReactElement,
  options: Omit<RenderOptions, "wrapper"> & ProvidersOptions = {},
) {
  const { client, ...renderOptions } = options
  const queryClient =
    client ??
    new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    })
  const result = rtlRender(ui, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
    ...renderOptions,
  })
  // Expose the QueryClient on the return value so tests that want to
  // call `queryClient.clear()` between assertions have a clean handle.
  return { ...result, queryClient }
}

// Re-export testing-library utilities so test files only need this
// single import for the rendered surface.
export {
  act,
  cleanup,
  fireEvent,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
