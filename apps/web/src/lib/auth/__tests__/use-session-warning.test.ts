import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import type { ArgsProps } from "antd/es/notification"

// ── Mocks ──────────────────────────────────────────────────────────

const mockNotificationWarning = vi.fn()
vi.mock("antd", () => ({
  notification: {
    warning: mockNotificationWarning,
  },
}))

const mockReplace = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
}))

const mockSessionTimerState = {
  expiresIn: 900 as number | null,
  expiresAt: "2026-08-02T12:00:00Z" as string | null,
  isExpiringSoon: false,
}
vi.mock("@/lib/auth/use-session-timer", () => ({
  useSessionTimer: () => mockSessionTimerState,
}))

// ── Helpers ────────────────────────────────────────────────────────

function setState(patch: Partial<typeof mockSessionTimerState>): void {
  Object.assign(mockSessionTimerState, patch)
}

/** Re-import the hook with fresh mocks each test */
async function importHook() {
  return (await import("@/lib/auth/use-session-warning")).useSessionWarning
}

// ── Tests ──────────────────────────────────────────────────────────

describe("useSessionWarning", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    setState({ expiresIn: 900, expiresAt: "2026-08-02T12:00:00Z", isExpiringSoon: false })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("does nothing when session has plenty of time", async () => {
    const useSessionWarning = await importHook()
    renderHook(() => useSessionWarning())

    expect(mockNotificationWarning).not.toHaveBeenCalled()
    expect(mockReplace).not.toHaveBeenCalled()
  })

  it("shows warning toast once when isExpiringSoon becomes true", async () => {
    const useSessionWarning = await importHook()
    const { rerender } = renderHook(() => useSessionWarning())

    // Transition to expiring soon
    setState({ expiresIn: 540, isExpiringSoon: true })
    await rerender()

    expect(mockNotificationWarning).toHaveBeenCalledTimes(1)
    const call = mockNotificationWarning.mock.calls[0]![0] as ArgsProps
    expect(call.key).toBe("session-expiring-soon")
    expect(call.message).toBe("会话即将过期")
    expect(call.description).toContain("9 分钟")
    expect(call.duration).toBe(0)
    expect(call.placement).toBe("topRight")

    // Re-render with same state — should NOT fire again
    await rerender()
    expect(mockNotificationWarning).toHaveBeenCalledTimes(1)
  })

  it("shows expired toast and redirects when session drops to null", async () => {
    const useSessionWarning = await importHook()
    const { rerender } = renderHook(() => useSessionWarning())

    // First establish authenticated state
    expect(mockSessionTimerState.expiresIn).not.toBeNull()
    await rerender()

    // Now simulate session expiry (401 clears expiresIn to null)
    setState({ expiresIn: null, expiresAt: null, isExpiringSoon: false })
    await rerender()

    expect(mockNotificationWarning).toHaveBeenCalledTimes(1)
    const call = mockNotificationWarning.mock.calls[0]![0] as ArgsProps
    expect(call.key).toBe("session-expired")
    expect(call.message).toBe("会话已过期")
    expect(call.description).toBe("请重新登录")
    expect(call.duration).toBe(0)

    // Advance past redirect delay
    act(() => {
      vi.advanceTimersByTime(3_000)
    })

    expect(mockReplace).toHaveBeenCalledWith("/admin/login")
  })

  it("does not show expired toast if never authenticated", async () => {
    // Start with null (never had a session)
    setState({ expiresIn: null, expiresAt: null, isExpiringSoon: false })

    const useSessionWarning = await importHook()
    renderHook(() => useSessionWarning())

    expect(mockNotificationWarning).not.toHaveBeenCalled()
    expect(mockReplace).not.toHaveBeenCalled()
  })

  it("expired toast fires only once even after multiple null re-renders", async () => {
    const useSessionWarning = await importHook()
    const { rerender } = renderHook(() => useSessionWarning())

    // Establish auth, then expire
    setState({ expiresIn: null, expiresAt: null, isExpiringSoon: false })
    await rerender()

    expect(mockNotificationWarning).toHaveBeenCalledTimes(1)

    // Re-render still expired — should NOT fire again
    await rerender()
    await rerender()
    expect(mockNotificationWarning).toHaveBeenCalledTimes(1)
  })
})
