/**
 * /admin/review/queue — AC-3 (default route is Layout A) + AC-4
 * (admin 兼任过渡期; future domain_expert role).
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const mockUseAuth = vi.fn()

vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => mockUseAuth(),
}))

const mockRouterReplace = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockRouterReplace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/lib/admin/review-queue-api", () => ({
  fetchReviewQueue: vi.fn().mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    pages: 1,
  }),
  submitReviewDecision: vi.fn(),
  NOTE_REQUIRED_ACTIONS: new Set(["dispute", "modify"]),
}))

import ReviewQueuePage, { canAccessReviewQueue } from "./page"

function renderWithClient(ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe("canAccessReviewQueue", () => {
  it("accepts admin (transitional role per AC-4)", () => {
    expect(canAccessReviewQueue("admin")).toBe(true)
  })

  it("accepts domain_expert (canonical reviewer role)", () => {
    expect(canAccessReviewQueue("domain_expert")).toBe(true)
  })

  it("accepts mixed-case role strings", () => {
    expect(canAccessReviewQueue("Admin")).toBe(true)
  })

  it("rejects editor / reviewer / null / unauthenticated", () => {
    expect(canAccessReviewQueue("editor")).toBe(false)
    expect(canAccessReviewQueue("reviewer")).toBe(false)
    expect(canAccessReviewQueue(null)).toBe(false)
    expect(canAccessReviewQueue(undefined)).toBe(false)
    expect(canAccessReviewQueue("")).toBe(false)
  })
})

describe("/admin/review/queue page", () => {
  beforeEach(() => {
    mockUseAuth.mockReset()
    mockRouterReplace.mockReset()
  })

  it("renders a loading spinner while the auth context resolves", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: true })
    renderWithClient(<ReviewQueuePage />)
    expect(document.querySelector(".ant-spin")).toBeInTheDocument()
  })

  it("redirects unauthenticated visitors to /login", async () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false })
    renderWithClient(<ReviewQueuePage />)
    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith("/login")
    })
  })

  it("redirects unauthorized roles (editor / reviewer) to /", async () => {
    mockUseAuth.mockReturnValue({
      user: { blog_role: "editor" },
      loading: false,
    })
    renderWithClient(<ReviewQueuePage />)
    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith("/")
    })
  })

  it("lets admin access the page (transitional period AC-4)", async () => {
    mockUseAuth.mockReturnValue({
      user: { blog_role: "admin" },
      loading: false,
    })
    renderWithClient(<ReviewQueuePage />)
    await waitFor(() => {
      expect(mockRouterReplace).not.toHaveBeenCalled()
    })
  })

  it("lets domain_expert access the page (canonical reviewer role)", async () => {
    mockUseAuth.mockReturnValue({
      user: { blog_role: "domain_expert" },
      loading: false,
    })
    renderWithClient(<ReviewQueuePage />)
    await waitFor(() => {
      expect(mockRouterReplace).not.toHaveBeenCalled()
    })
  })
})
