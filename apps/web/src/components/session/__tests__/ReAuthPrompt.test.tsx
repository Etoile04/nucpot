/**
 * Tests for ReAuthPrompt — explicit modal that appears when the
 * SessionManager transitions to ``expired``.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { render, screen, cleanup, fireEvent } from "@testing-library/react"
import { ConfigProvider } from "antd"

// Mock next/navigation so useRouter/usePathname resolve in jsdom.
const routerReplace = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace }),
  usePathname: () => "/literature",
}))

import { SessionProvider, ReAuthPrompt } from "@/components/session"
import {
  SessionManager,
  type SessionManagerOptions,
} from "@/lib/session-manager"
import type { RefreshResponse } from "@/lib/session-manager"

interface Harness {
  manager: SessionManager
  fetchRefresh: ReturnType<typeof vi.fn>
  fetchMe: ReturnType<typeof vi.fn>
}

function createHarness(): Harness {
  const fetchRefresh = vi.fn<
    Parameters<SessionManagerOptions["fetchRefresh"]>,
    ReturnType<SessionManagerOptions["fetchRefresh"]>
  >()
  const fetchMe = vi.fn<
    Parameters<SessionManagerOptions["fetchMe"]>,
    ReturnType<SessionManagerOptions["fetchMe"]>
  >()
  const manager = new SessionManager({ fetchRefresh, fetchMe })
  return { manager, fetchRefresh, fetchMe }
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
  })

  afterEach(() => {
    harness.manager.shutdown()
    cleanup()
    routerReplace.mockClear()
  })

  it("renders nothing when authenticated", async () => {
    harness.fetchMe.mockResolvedValueOnce({
      expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    })
    renderPrompt(harness)
    await new Promise((r) => setTimeout(r, 5))
    expect(screen.queryByTestId("reauth-prompt")).toBeNull()
  })

  it("shows the modal when state becomes expired", async () => {
    harness.fetchMe.mockResolvedValueOnce({
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
})