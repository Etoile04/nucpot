/**
 * Auth-aware fetch wrapper.
 *
 * NFM-2252 acceptance criteria:
 *   - 401 response from a data endpoint → force refresh, then retry once.
 *   - N concurrent 401s share ONE in-flight refresh (single-flight via
 *     AuthSessionStore.refresh()).
 *   - Refresh failure: callers get rejected, onReauthRequired is invoked
 *     exactly once, and no retry is attempted.
 *   - Auth endpoints (/auth/*) skip the refresh layer to avoid recursion.
 *
 * The token itself is an HttpOnly cookie set by the server, so the wrapper
 * just forwards `credentials: "include"`. The wrapper NEVER reads the
 * token — it only observes the session *state* via AuthSessionStore.
 */

import { AuthSessionStore } from "./auth-session"

export interface AuthFetchOptions {
  /** Session store that owns the single-flight refresh promise. */
  readonly store: AuthSessionStore
  /** Underlying fetch implementation (real `fetch` in browser; injected in tests). */
  readonly fetch: typeof fetch
  /** Endpoint prefix used to recognize auth paths to skip. Default "/api/v1". */
  readonly endpoint?: string
  /**
   * Invoked exactly once when a refresh fails. The re-auth modal (NFM-2254
   * task C) hooks this to mount itself.
   */
  readonly onReauthRequired?: () => void
}

export type AuthFetch = (
  input: RequestInfo,
  init?: RequestInit,
) => Promise<Response>

const DEFAULT_ENDPOINT = "/api/v1"

const isAuthEndpoint = (url: string, endpoint: string): boolean => {
  if (url === `${endpoint}/auth`) return true
  return url.startsWith(`${endpoint}/auth/`)
}

const buildInit = (init: RequestInit | undefined): RequestInit => {
  return {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...((init?.headers as Record<string, string> | undefined) ?? {}),
    },
  }
}

/**
 * Wrap a fetch with auth-aware behavior. Returns a function with the same
 * signature as the global `fetch` so callers can drop it in.
 *
 * Concurrency model:
 *   - Each caller independently runs `fetch → maybe refresh → maybe retry`.
 *   - The refresh step delegates to AuthSessionStore.refresh(), which
 *     coalesces N concurrent refresh calls into a single in-flight request.
 *   - A 401 from `/auth/*` endpoints does NOT trigger refresh (would loop).
 *   - Non-401 errors pass through unchanged.
 */
export function createAuthFetch(options: AuthFetchOptions): AuthFetch {
  const {
    store,
    endpoint = DEFAULT_ENDPOINT,
    onReauthRequired,
  } = options

  // Single re-auth signal — observed by the re-auth modal.
  let reauthSignaled = false

  const signalReauth = (): void => {
    if (reauthSignaled) return
    reauthSignaled = true
    onReauthRequired?.()
  }

  const attempt = async (
    url: string,
    init: RequestInit | undefined,
  ): Promise<Response> => {
    // Look up `options.fetch` lazily so callers (tests) can swap the
    // underlying implementation without rebuilding the wrapper.
    const underlyingFetch = options.fetch
    let res = await underlyingFetch(url, buildInit(init))

    // Non-401 error: surface the response unchanged; the original
    // `api-client.request()` throws on any !ok so consumers get a
    // descriptive error rather than a raw Response.
    if (res.status === 204) return res
    if (!res.ok && res.status !== 401) {
      throw await buildHttpError(res, `请求失败 (${res.status})`)
    }

    if (res.status !== 401) return res

    // 401 on an auth endpoint — do not retry; just surface the error.
    if (isAuthEndpoint(url, endpoint)) {
      throw await buildHttpError(res, "认证已过期，请重新登录后重试")
    }

    // Force refresh. The store coalesces concurrent callers.
    try {
      await store.refresh()
    } catch {
      // Refresh failed; signal re-auth once and reject the original caller.
      signalReauth()
      throw await buildHttpError(res, "认证已过期，请重新登录后重试")
    }

    // Retry once with the same init.
    res = await options.fetch(url, buildInit(init))
    if (res.status === 204) return res
    if (!res.ok) {
      throw await buildHttpError(res, `请求失败 (${res.status})`)
    }
    return res
  }

  return async (input: RequestInfo, init?: RequestInit): Promise<Response> => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url
    return attempt(url, init)
  }
}

async function buildHttpError(res: Response, fallback: string): Promise<Error> {
  const body = (await res.json().catch(() => null)) as
    | { detail?: string; message?: string }
    | null
  const message = body?.detail ?? body?.message ?? fallback
  return new Error(message)
}