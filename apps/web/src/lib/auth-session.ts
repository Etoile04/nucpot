/**
 * AuthSessionStore — observable session manager for the frontend.
 *
 * NFM-2252 acceptance criteria:
 *   - Auto-refresh fires at `safetyMarginFraction` of lifetime before expiry.
 *   - N concurrent refresh() callers share exactly one in-flight network call.
 *   - Refresh failure transitions state to `expired`; subscribers notified.
 *   - getRemainingSeconds() returns 0 once expired.
 *   - shutdown() clears any pending timer (no leaked intervals).
 *   - The module is framework-agnostic — no React, no DOM. Wiring is done via
 *     the constructor so tests can inject a fake clock and fake fetchers.
 *
 * The token value itself lives in an HttpOnly cookie set by the server, so
 * this store tracks session *state* (authenticated / refreshing / expired)
 * and the next-refresh boundary — not the raw token. The network layer
 * reads session state to decide when to force a refresh; UI consumers
 * subscribe to render countdown.
 */

export interface SessionResponse {
  readonly expires_at: string // ISO 8601
}

export interface RefreshResponse {
  readonly expires_at: string // ISO 8601
}

export type SessionState =
  | { readonly kind: "unauthenticated" }
  | {
      readonly kind: "authenticated"
      readonly expiresAt: number // epoch ms
      readonly nextRefreshAt: number // epoch ms — when auto-refresh fires
    }
  | { readonly kind: "refreshing"; readonly expiresAt: number }
  | { readonly kind: "expired" }

export type SessionSubscriber = (state: SessionState) => void

export interface AuthSessionStoreOptions {
  /** Hit `GET /api/v1/auth/session` to bootstrap expiry; return null when not signed in. */
  readonly fetchSession: (input: RequestInfo) => Promise<SessionResponse | null>
  /** Hit `POST /api/v1/auth/refresh` and return its body. */
  readonly fetchRefresh: (input: RequestInfo) => Promise<RefreshResponse>
  /** Fraction of remaining lifetime before which refresh fires. Default 0.2. */
  readonly safetyMarginFraction?: number
  /** Injectable clock + timer functions for tests. */
  readonly setTimeoutFn?: (cb: () => void, ms: number) => unknown
  readonly clearTimeoutFn?: (id: unknown) => void
  readonly nowFn?: () => number
}

export class AuthSessionStore {
  private state: SessionState = { kind: "unauthenticated" }
  private readonly subscribers = new Set<SessionSubscriber>()
  private readonly fetchSession: (input: RequestInfo) => Promise<SessionResponse | null>
  private readonly fetchRefresh: (input: RequestInfo) => Promise<RefreshResponse>
  private readonly safetyMarginFraction: number
  private readonly setTimeoutFn: (cb: () => void, ms: number) => unknown
  private readonly clearTimeoutFn: (id: unknown) => void
  private readonly nowFn: () => number

  /** Current refresh timer handle, or null when no timer is pending. */
  private refreshTimer: unknown = null
  /** In-flight refresh Promise shared by all concurrent callers. */
  private inFlightRefresh: Promise<void> | null = null
  /** Cached expiry so we can compute lifetime → safety margin. */
  private bootstrapExpiresAt: number | null = null
  /** `now` at the moment init() captured the bootstrap expiry. */
  private bootstrapNow: number | null = null
  /** Guards against late callbacks after shutdown(). */
  private isShutdown = false

  constructor(options: AuthSessionStoreOptions) {
    this.fetchSession = options.fetchSession
    this.fetchRefresh = options.fetchRefresh
    this.safetyMarginFraction = options.safetyMarginFraction ?? 0.2
    this.setTimeoutFn = options.setTimeoutFn ?? ((cb, ms) => setTimeout(cb, ms))
    this.clearTimeoutFn =
      options.clearTimeoutFn ?? ((id) => clearTimeout(id as number))
    this.nowFn = options.nowFn ?? (() => Date.now())
  }

  /** Bootstrap expiry via /auth/session. Idempotent. */
  async init(): Promise<void> {
    if (this.isShutdown) return
    const me = await this.fetchSession("/api/v1/auth/session")
    if (!me) {
      this.transition({ kind: "unauthenticated" })
      return
    }
    const expiresAt = Date.parse(me.expires_at)
    if (Number.isNaN(expiresAt)) {
      this.transition({ kind: "unauthenticated" })
      return
    }
    // Capture the *initial* lifetime so we can keep the same safety-margin
    // window across refresh cycles (the server re-issues with the same TTL).
    const now = this.nowFn()
    if (this.bootstrapExpiresAt === null) {
      this.bootstrapExpiresAt = expiresAt
      this.bootstrapNow = now
    }
    this.scheduleFromExpiry(expiresAt)
  }

  /** Current public state. */
  getState(): SessionState {
    return this.state
  }

  /** Seconds remaining until the access token expires. 0 when not authenticated. */
  getRemainingSeconds(): number {
    const expiresAt = this.currentExpiresAt()
    if (expiresAt === null) return 0
    const ms = expiresAt - this.nowFn()
    return ms <= 0 ? 0 : Math.ceil(ms / 1000)
  }

  /**
   * Force a refresh now. Concurrent callers share one in-flight Promise.
   * Rejects with the underlying network error if refresh fails (state → expired).
   */
  refresh(): Promise<void> {
    if (this.isShutdown) return Promise.reject(new Error("store is shut down"))
    if (this.inFlightRefresh) return this.inFlightRefresh
    this.inFlightRefresh = this.runRefresh().finally(() => {
      this.inFlightRefresh = null
    })
    return this.inFlightRefresh
  }

  /** Subscribe to state transitions. Returns an unsubscribe function. */
  subscribe(subscriber: SessionSubscriber): () => void {
    this.subscribers.add(subscriber)
    return () => {
      this.subscribers.delete(subscriber)
    }
  }

  /** Cancel any pending timer. Idempotent. */
  shutdown(): void {
    this.isShutdown = true
    if (this.refreshTimer !== null) {
      this.clearTimeoutFn(this.refreshTimer)
      this.refreshTimer = null
    }
  }

  // ── private ─────────────────────────────────────────────────────────

  private async runRefresh(): Promise<void> {
    const expiresAt = this.currentExpiresAt()
    if (expiresAt !== null) {
      this.transition({ kind: "refreshing", expiresAt })
    }
    try {
      const res = await this.fetchRefresh("/api/v1/auth/refresh")
      const newExpiresAt = Date.parse(res.expires_at)
      if (Number.isNaN(newExpiresAt)) {
        throw new Error("refresh response missing valid expires_at")
      }
      if (this.isShutdown) return
      this.scheduleFromExpiry(newExpiresAt)
    } catch (err) {
      // Refresh failed — cancel any pending auto-refresh and surface the
      // `expired` state so the UI can prompt the user to re-auth.
      // We do NOT retry: the caller (or a fresh login) must clear this state.
      if (this.refreshTimer !== null) {
        this.clearTimeoutFn(this.refreshTimer)
        this.refreshTimer = null
      }
      if (!this.isShutdown) {
        this.transition({ kind: "expired" })
      }
      throw err
    }
  }

  private scheduleFromExpiry(expiresAt: number): void {
    const now = this.nowFn()
    // Margin is anchored to the *original* lifetime captured at init(). The
    // backend re-issues with the same TTL, so the absolute margin stays
    // constant across refresh cycles instead of compounding.
    const baselineLifetime =
      this.bootstrapExpiresAt !== null && this.bootstrapNow !== null
        ? Math.max(1, this.bootstrapExpiresAt - this.bootstrapNow)
        : Math.max(1, expiresAt - now)
    const marginMs = Math.max(0, baselineLifetime * this.safetyMarginFraction)
    const nextRefreshAt = expiresAt - marginMs
    this.transition({ kind: "authenticated", expiresAt, nextRefreshAt })
    const delay = Math.max(0, nextRefreshAt - now)
    if (this.refreshTimer !== null) {
      this.clearTimeoutFn(this.refreshTimer)
    }
    this.refreshTimer = this.setTimeoutFn(() => {
      this.refreshTimer = null
      // Swallow rejection here — subscribers got the `expired` transition.
      void this.refresh().catch(() => undefined)
    }, delay)
  }

  private currentExpiresAt(): number | null {
    switch (this.state.kind) {
      case "authenticated":
      case "refreshing":
        return this.state.expiresAt
      default:
        return null
    }
  }

  private transition(next: SessionState): void {
    if (this.isShutdown) return
    this.state = next
    for (const sub of this.subscribers) {
      try {
        sub(next)
      } catch {
        // Subscriber errors must not poison other subscribers or the store.
      }
    }
  }
}

/**
 * Default factory wiring AuthSessionStore to the real `fetch` + browser clock.
 * Use this from React providers; tests should construct AuthSessionStore directly.
 */
export function createBrowserAuthSessionStore(): AuthSessionStore {
  const jsonFetch = async <T>(
    input: RequestInfo,
    init?: RequestInit,
  ): Promise<T> => {
    const res = await fetch(input, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    })
    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText}`)
    }
    return (await res.json()) as T
  }

  return new AuthSessionStore({
    fetchSession: async (input): Promise<SessionResponse | null> => {
      try {
        return await jsonFetch<SessionResponse>(input)
      } catch {
        return null
      }
    },
    fetchRefresh: async (input) =>
      jsonFetch<RefreshResponse>(input, { method: "POST" }),
  })
}