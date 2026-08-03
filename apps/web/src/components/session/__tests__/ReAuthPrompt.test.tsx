/**
 * Tests for ReAuthPrompt — explicit modal that appears when the
 * SessionManager transitions to ``expired``.
 *
 * NFM-2254 acceptance criteria:
 *   - Modal renders within 1s of refresh-failure transition.
 *   - "Sign in again" routes to /admin/login?returnTo=<pathname>.
 *   - In-flight form state is preserved across the modal opening
 *     (modal must NOT unmount the form beneath it).
 *   - Esc and mask-click do not dismiss the modal (NFM-2251 §b).
 *   - The "returnTo" carries the FULL current URL, including the
 *     query string (NFM-2254 AC: "capturing the current URL").
 *   - There is exactly one actionable button in the modal —
 *     no "Later"/"稍后" cancel affordance (NFM-2251 §b:
 *     non-dismissible surface).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { render, screen, cleanup, fireEvent, act } from "@testing-library/react"
import { ConfigProvider } from "antd"

// Mock next/navigation so useRouter/usePathname/useSearchParams
// resolve in jsdom. The pathname and search string are mutable
// per-test so we can drive the returnTo round-trip below.
const routerReplace = vi.fn()
let mockedPathname = "/literature"
let mockedSearch = ""
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace }),
  usePathname: () => mockedPathname,
  useSearchParams: () => new URLSearchParams(mockedSearch),
}))

import { SessionProvider, ReAuthPrompt } from "@/components/session"
import {
  SessionManager,
  type SessionManagerOptions,
} from "@/lib/session-manager"

type FetchRefresh = SessionManagerOptions["fetchRefresh"]
type FetchMe = SessionManagerOptions["fetchSession"]

interface Harness {
  manager: SessionManager
  fetchRefresh: ReturnType<typeof vi.fn>
  fetchSession: ReturnType<typeof vi.fn>
}

function createHarness(): Harness {
  const fetchRefresh = vi.fn() as unknown as ReturnType<typeof vi.fn> &
    FetchRefresh
  const fetchSession = vi.fn() as unknown as ReturnType<typeof vi.fn> & FetchMe
  const manager = new SessionManager({ fetchRefresh, fetchSession })
  return { manager, fetchRefresh, fetchSession }
}

function renderPrompt(harness: Harness) {
  return render(
    <ConfigProvider>
      <SessionProvider manager={harness.manager}>
        <ReAuthPrompt />
      </SessionProvider>
    </ConfigProvider>,
  )
}

describe("<ReAuthPrompt />", () => {
  let harness: Harness

  beforeEach(() => {
    harness = createHarness()
    mockedPathname = "/literature"
    mockedSearch = ""
  })

  afterEach(() => {
    harness.manager.shutdown()
    cleanup()
    routerReplace.mockClear()
    // jsdom's window.location.hash persists across tests — clear it
    // so the round-trip test starts deterministic.
    if (typeof window !== "undefined") {
      window.history.replaceState(null, "", window.location.pathname + window.location.search)
    }
  })

  it("renders nothing when authenticated", async () => {
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    renderPrompt(harness)
    await new Promise((r) => setTimeout(r, 5))
    expect(screen.queryByTestId("reauth-prompt")).toBeNull()
  })

  it("shows the modal when state becomes expired", async () => {
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    harness.fetchRefresh.mockRejectedValueOnce(new Error("revoked"))
    renderPrompt(harness)
    await new Promise((r) => setTimeout(r, 5))
    await harness.manager.refresh().catch(() => undefined)
    await new Promise((r) => setTimeout(r, 5))
    const el = screen.queryByTestId("reauth-prompt")
    expect(el).not.toBeNull()
    expect(el?.textContent ?? "").toMatch(/会话已过期/)
  })

  it("appears within 1s of refresh-failure transition (NFM-2254 SLA)", async () => {
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    renderPrompt(harness)
    await new Promise((r) => setTimeout(r, 5))

    // Now trigger refresh failure and time the modal appearance.
    let modalAtMs: number | null = null
    const deadline = Date.now() + 1000

    // Poll for modal presence every 16ms until it appears or we exceed the SLA.
    harness.fetchRefresh.mockRejectedValueOnce(new Error("revoked"))
    const start = Date.now()
    void harness.manager.refresh().catch(() => undefined)

    while (Date.now() < deadline) {
      if (screen.queryByTestId("reauth-prompt")) {
        modalAtMs = Date.now() - start
        break
      }
      await new Promise((r) => setTimeout(r, 16))
    }
    expect(modalAtMs).not.toBeNull()
    expect(modalAtMs!).toBeLessThan(1000)
  })

  it("routes to /admin/login with returnTo=<pathname> when Re-login is clicked", async () => {
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    harness.fetchRefresh.mockRejectedValueOnce(new Error("revoked"))
    renderPrompt(harness)
    await new Promise((r) => setTimeout(r, 5))
    await harness.manager.refresh().catch(() => undefined)
    await new Promise((r) => setTimeout(r, 20))

    // Find the OK button ("重新登录") and click it.
    const okBtn = screen.getByRole("button", { name: /重新登录/ })
    await act(async () => {
      fireEvent.click(okBtn)
    })

    expect(routerReplace).toHaveBeenCalledTimes(1)
    const calledUrl = routerReplace.mock.calls[0]?.[0] as string
    expect(calledUrl).toMatch(/^\/admin\/login\?returnTo=/)
    // pathname mocked to /literature with no search params —
    // returnTo must be the URL-encoded pathname.
    expect(calledUrl).toContain(encodeURIComponent("/literature"))
  })

  it("captures the query string in returnTo (NFM-2254 AC)", async () => {
    // Drive the round-trip case the previous revision silently
    // dropped: a user on a filtered, paginated view whose session
    // dies must return to that exact filtered view, not page 1.
    mockedPathname = "/literature"
    mockedSearch = "page=3&status=pending"
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    harness.fetchRefresh.mockRejectedValueOnce(new Error("revoked"))
    renderPrompt(harness)
    await new Promise((r) => setTimeout(r, 5))
    await harness.manager.refresh().catch(() => undefined)
    await new Promise((r) => setTimeout(r, 20))

    const okBtn = screen.getByRole("button", { name: /重新登录/ })
    await act(async () => {
      fireEvent.click(okBtn)
    })

    expect(routerReplace).toHaveBeenCalledTimes(1)
    const calledUrl = routerReplace.mock.calls[0]?.[0] as string
    expect(calledUrl).toMatch(/^\/admin\/login\?returnTo=/)
    // The full path-with-query must be URL-encoded into returnTo.
    expect(calledUrl).toContain(
      encodeURIComponent("/literature?page=3&status=pending"),
    )
  })

  it("renders exactly one actionable button — no 稍后 cancel affordance (NFM-2251 §b)", async () => {
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    harness.fetchRefresh.mockRejectedValueOnce(new Error("revoked"))
    renderPrompt(harness)
    await new Promise((r) => setTimeout(r, 5))
    await harness.manager.refresh().catch(() => undefined)
    await new Promise((r) => setTimeout(r, 20))

    const allButtons = screen.getAllByRole("button")
    const actionable = allButtons.filter(
      (b) => !b.getAttribute("aria-label")?.includes("Close"),
    )
    expect(actionable).toHaveLength(1)
    expect(actionable[0]).toHaveTextContent(/重新登录/)
    // No 稍后 ("Later") anywhere on the modal surface.
    expect(screen.queryByText(/稍后/)).toBeNull()
  })

  it("Esc does not dismiss the modal (NFM-2251 §b)", async () => {
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    harness.fetchRefresh.mockRejectedValueOnce(new Error("revoked"))
    renderPrompt(harness)
    await new Promise((r) => setTimeout(r, 5))
    await harness.manager.refresh().catch(() => undefined)
    await new Promise((r) => setTimeout(r, 5))

    expect(screen.queryByTestId("reauth-prompt")).not.toBeNull()

    // Press Escape on the modal — it must remain visible.
    fireEvent.keyDown(document.body, { key: "Escape" })
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByTestId("reauth-prompt")).not.toBeNull()
  })

  it("does not unmount sibling content while the modal is open", async () => {
    // Sibling form content must remain mounted while the modal is up —
    // this is what preserves in-flight form state across the modal opening.
    harness.fetchSession.mockResolvedValueOnce({
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    harness.fetchRefresh.mockRejectedValueOnce(new Error("revoked"))

    render(
      <ConfigProvider>
        <SessionProvider manager={harness.manager}>
          <div>
            <input data-testid="in-flight-input" defaultValue="draft text" />
            <ReAuthPrompt />
          </div>
        </SessionProvider>
      </ConfigProvider>,
    )
    await new Promise((r) => setTimeout(r, 5))
    await harness.manager.refresh().catch(() => undefined)
    await new Promise((r) => setTimeout(r, 5))

    expect(screen.queryByTestId("reauth-prompt")).not.toBeNull()
    const input = screen.queryByTestId("in-flight-input")
    expect(input).not.toBeNull()
    expect((input as HTMLInputElement).value).toBe("draft text")
  })
})
