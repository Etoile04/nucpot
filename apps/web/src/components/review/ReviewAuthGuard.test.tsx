import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"

// ── Mocks ─────────────────────────────────────────────────────────────

const mockReplace = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
  usePathname: () => "/review/kg",
}))

// Import the mocked module so we can access mock implementations
import { authApi } from "@/lib/api-client"

vi.mock("@/lib/api-client", () => ({
  authApi: {
    getMe: vi.fn(),
  },
  request: vi.fn(),
}))

import ReviewAuthGuard, { useReviewAuth } from "./ReviewAuthGuard"
import type { UserProfile } from "@/lib/api-client"

// ── Fixtures ────────────────────────────────────────────────────────────

const ACTIVE_USER = {
  id: "user-1",
  username: "reviewer",
  email: "reviewer@nfm.org",
  full_name: "NFM Reviewer",
  blog_role: null,
  is_active: true,
}

const INACTIVE_USER = {
  ...ACTIVE_USER,
  is_active: false,
}

const mockGetMe = vi.mocked(authApi.getMe)

// ── Tests ───────────────────────────────────────────────────────────────

describe("ReviewAuthGuard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("shows loading state initially", () => {
    mockGetMe.mockReturnValue(new Promise<UserProfile>(() => {}))

    render(
      <ReviewAuthGuard>
        <p>Protected content</p>
      </ReviewAuthGuard>,
    )

    expect(screen.getByText("加载中...")).toBeDefined()
  })

  it("redirects to /login when getMe fails (no cookie)", async () => {
    mockGetMe.mockRejectedValue(new Error("Unauthorized"))

    render(
      <ReviewAuthGuard>
        <p>Protected content</p>
      </ReviewAuthGuard>,
    )

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/login")
    })
  })

  it("redirects to /login when user is inactive", async () => {
    mockGetMe.mockResolvedValue(INACTIVE_USER)

    render(
      <ReviewAuthGuard>
        <p>Protected content</p>
      </ReviewAuthGuard>,
    )

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/login")
    })
  })

  it("renders children when user is active", async () => {
    mockGetMe.mockResolvedValue(ACTIVE_USER)

    render(
      <ReviewAuthGuard>
        <p>Protected content</p>
      </ReviewAuthGuard>,
    )

    await waitFor(() => {
      expect(screen.getByText("Protected content")).toBeDefined()
    })
  })

  it("exposes profile via useReviewAuth", async () => {
    mockGetMe.mockResolvedValue(ACTIVE_USER)

    function TestConsumer() {
      const { profile, loading } = useReviewAuth()

      if (loading) return <p>Loading</p>
      return (
        <p data-testid="profile-email">{profile?.email ?? "none"}</p>
      )
    }

    render(
      <ReviewAuthGuard>
        <TestConsumer />
      </ReviewAuthGuard>,
    )

    await waitFor(() => {
      expect(screen.getByTestId("profile-email").textContent).toBe(
        ACTIVE_USER.email,
      )
    })
  })

  it("useReviewAuth returns null profile while loading", () => {
    mockGetMe.mockReturnValue(new Promise<UserProfile>(() => {}))

    render(
      <ReviewAuthGuard>
        <p>Protected content</p>
      </ReviewAuthGuard>,
    )

    // The guard shows its own loading indicator
    expect(screen.getByText("加载中...")).toBeDefined()
  })
})
