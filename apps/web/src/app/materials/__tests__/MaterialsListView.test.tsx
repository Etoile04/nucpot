import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { MaterialsListView } from "../MaterialsListView"

// ── Mocks ──────────────────────────────────────────────────────────────

const mockMaterials = [
  {
    id: "mat-001",
    name: "UO₂",
    formula: "UO2",
    crystal_structure: "fluorite",
    description: "Uranium dioxide",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  },
  {
    id: "mat-002",
    name: "Zr",
    formula: "Zr",
    crystal_structure: "hcp",
    description: "Zirconium",
    is_active: true,
    created_at: "2026-01-03T00:00:00Z",
    updated_at: "2026-01-04T00:00:00Z",
  },
]

const mockRequest = vi.fn()

vi.mock("@/lib/api-client", () => ({
  request: (...args: unknown[]) => mockRequest(...args),
}))

function mockRequestSuccess(items = mockMaterials, total = 2) {
  mockRequest.mockResolvedValue({
    success: true,
    data: { items, total, page: 1, per_page: 20 },
  })
}

function mockRequestError() {
  mockRequest.mockRejectedValue(new Error("Network error"))
}

function renderComponent() {
  return render(<MaterialsListView />)
}

// ── Tests ──────────────────────────────────────────────────────────────

describe("MaterialsListView", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("renders title and description", async () => {
    mockRequestSuccess()
    renderComponent()
    expect(screen.getByText("材料列表")).toBeDefined()
  })

  it("loads and displays materials", async () => {
    mockRequestSuccess()
    renderComponent()

    // Fast-forward the debounce timer
    vi.advanceTimersByTime(500)
    vi.useRealTimers()

    await waitFor(() => {
      expect(mockRequest).toHaveBeenCalled()
      expect(screen.getByText("UO₂")).toBeDefined()
    })
  })

  it("shows empty state when no materials", async () => {
    mockRequestSuccess([], 0)
    renderComponent()

    vi.advanceTimersByTime(500)
    vi.useRealTimers()

    await waitFor(() => {
      expect(screen.getByText("暂无材料数据")).toBeDefined()
    })
  })

  it("shows error on API failure", async () => {
    mockRequestError()
    renderComponent()

    vi.advanceTimersByTime(500)
    vi.useRealTimers()

    await waitFor(() => {
      expect(screen.getByText("Network error")).toBeDefined()
    })
  })

  it("links to material detail pages", async () => {
    mockRequestSuccess()
    const { container } = renderComponent()

    vi.advanceTimersByTime(500)
    vi.useRealTimers()

    await waitFor(() => {
      const links = container.querySelectorAll('a[href*="/materials/"]')
      expect(links.length).toBeGreaterThan(0)
      expect(
        Array.from(links).some((l) =>
          (l as HTMLAnchorElement).href.includes("/materials/mat-001"),
        ),
      ).toBe(true)
    })
  })
})
