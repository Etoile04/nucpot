import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import ReviewAuthGuard, { useReviewAuth } from "./ReviewAuthGuard"

// ── Mocks ─────────────────────────────────────────────────────────────

const mockReplace = vi.fn()
const mockGetToken = vi.fn()
const mockClearToken = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
}))

vi.mock("@/lib/api-client", () => ({
  getToken: (): string | null => mockGetToken(),
  clearToken: (): void => mockClearToken(),
  authApi: {
    getMe: vi.fn(),
  },
  request: vi.fn(),
}))

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

// ── Tests ───────────────────────────────────────────────────────────────

describe("ReviewAuthGuard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("shows loading state initially", () => {
    mockGetToken.mockReturnValue("valid-token")

    render(
      <ReviewAuthGuard>
        <p>Protected content</p>
      </ReviewAuthGuard>,
    )

    expect(screen.getByText("加载中...")).toBeDefined()
  })

  it("redirects to /login when no token exists", async () => {
    mockGetToken.mockReturnValue(null)

    render(
      <ReviewAuthGuard>
        <p>Protected content</p>
      </ReviewAuthGuard>,
    )

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/login")
    })
  })

  it("redirects to /login when getMe fails", async () => {
    mockGetToken.mockReturnValue("valid-token")

    const { authApi } = await import("@/lib/api-client")
    vi.mocked(authApi.getMe).mockRejectedValue(new Error("Unauthorized"))

    render(
      <ReviewAuthGuard>
        <p>Protected content</p>
      </ReviewAuthGuard>,
    )

    await waitFor(() => {
      expect(mockClearToken).toHaveBeenCalled()
      expect(mockReplace).toHaveBeenCalledWith("/login")
    })
  })

  it("redirects to /login when user is inactive", async () => {
    mockGetToken.mockReturnValue("valid-token")

    const { authApi } = await import("@/lib/api-client")
    vi.mocked(authApi.getMe).mockResolvedValue(INACTIVE_USER)

    render(
      <ReviewAuthGuard>
        <p>Protected content</p>
      </ReviewAuthGuard>,
    )

    await waitFor(() => {
      expect(mockClearToken).toHaveBeenCalled()
      expect(mockReplace).toHaveBeenCalledWith("/login")
    })
  })

  it("renders children when user is active", async () => {
    mockGetToken.mockReturnValue("valid-token")

    const { authApi } = await import("@/lib/api-client")
    vi.mocked(authApi.getMe).mockResolvedValue(ACTIVE_USER)

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
    mockGetToken.mockReturnValue("valid-token")

    const { authApi } = await import("@/lib/api-client")
    vi.mocked(authApi.getMe).mockResolvedValue(ACTIVE_USER)

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
    mockGetToken.mockReturnValue("valid-token")

    function TestConsumer() {
      const { profile, loading } = useReviewAuth()
      return (
        <p>
          {loading ? "loading" : "loaded"} |{" "}
          {profile ? profile.username : "null"}
        </p>
      )
    }

    render(
      <ReviewAuthGuard>
        <TestConsumer />
      </ReviewAuthGuard>,
    )

    expect(screen.getByText(/loading/).textContent).toContain("loading")
  })
})
