/**
 * ReviewQueueContent — Layout A table (spec §4.2).
 *
 * Verifies:
 *   • confidence asc ordering (low-confidence first) — AC-3
 *   • all six required columns render
 *   • row click opens the shared 5-action drawer — AC-4
 *   • refresh button refetches the data
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { fetchReviewQueue } from "@/lib/admin/review-queue-api"
import { ReviewQueueContent } from "./ReviewQueueContent"

vi.mock("@/lib/admin/review-queue-api", () => ({
  fetchReviewQueue: vi.fn(),
  submitReviewDecision: vi.fn(),
  NOTE_REQUIRED_ACTIONS: new Set(["dispute", "modify"]),
}))

const mockedFetch = vi.mocked(fetchReviewQueue)

const lowRow = {
  id: "row-low",
  itemType: "measurement" as const,
  confidence: 0.32,
  reviewStatus: "pending",
  source: { paragraph: "low confidence paragraph", page: 1, doi: null },
  createdAt: "2026-09-10T00:00:00Z",
  valueScalar: 0.32,
  unitId: "unit-1",
  notes: null,
  propertyTypeId: null,
  datasetId: null,
}

const highRow = {
  id: "row-high",
  itemType: "measurement" as const,
  confidence: 0.95,
  reviewStatus: "pending",
  source: { paragraph: "high confidence paragraph", page: 2, doi: null },
  createdAt: "2026-09-10T00:00:00Z",
  valueScalar: 1.23,
  unitId: "unit-2",
  notes: null,
  propertyTypeId: null,
  datasetId: null,
}

function renderWithQuery(ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe("ReviewQueueContent — Layout A", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders all six spec §4.2 columns when data loads", async () => {
    mockedFetch.mockResolvedValueOnce({
      items: [lowRow, highRow],
      total: 2,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      expect(screen.getByText("属性")).toBeInTheDocument()
      expect(screen.getByText("值")).toBeInTheDocument()
      expect(screen.getByText("单位")).toBeInTheDocument()
      expect(screen.getByText("置信度")).toBeInTheDocument()
      expect(screen.getByText("来源段落")).toBeInTheDocument()
      expect(screen.getByText("状态")).toBeInTheDocument()
    })
  })

  it("sorts rows by confidence asc (low first) per spec §4.2", async () => {
    // Server returns high first; queue must re-order low-first.
    mockedFetch.mockResolvedValueOnce({
      items: [highRow, lowRow],
      total: 2,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      const cells = document.querySelectorAll(".ant-table-tbody tr td:nth-child(2)")
      // First row's value column should be 0.32 (low), not 1.23 (high)
      expect(cells[0]?.textContent).toContain("0.32")
      expect(cells[1]?.textContent).toContain("1.23")
    })
  })

  it("renders an Empty state when there are no pending rows", async () => {
    mockedFetch.mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      expect(screen.getByText(/暂无待校对行/)).toBeInTheDocument()
    })
  })

  it("opens the drawer on row click and shows all five actions (AC-4)", async () => {
    mockedFetch.mockResolvedValueOnce({
      items: [lowRow, highRow],
      total: 2,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      expect(screen.getByText("low confidence paragraph")).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText("low confidence paragraph"))
    await waitFor(() => {
      expect(screen.getByTestId("review-action-confirm")).toBeInTheDocument()
      expect(screen.getByTestId("review-action-modify")).toBeInTheDocument()
      expect(screen.getByTestId("review-action-invalid")).toBeInTheDocument()
      expect(screen.getByTestId("review-action-dispute")).toBeInTheDocument()
      expect(screen.getByTestId("review-action-skip")).toBeInTheDocument()
    })
  })

  it("refetches when the refresh button is clicked", async () => {
    mockedFetch.mockResolvedValue({
      items: [lowRow],
      total: 1,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      expect(screen.getByTestId("review-queue-refresh")).toBeInTheDocument()
    })
    // Wait for the first (mount-time) fetch to settle so its refetch
    // counter doesn't race with the click-triggered one.
    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledTimes(1)
    })
    const callsBefore = mockedFetch.mock.calls.length
    fireEvent.click(screen.getByTestId("review-queue-refresh"))
    await waitFor(
      () => {
        expect(mockedFetch.mock.calls.length).toBeGreaterThan(callsBefore)
      },
      { timeout: 5000 },
    )
  })

  it("renders an error alert when the API call fails", async () => {
    mockedFetch.mockRejectedValueOnce(new Error("网络超时"))
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      expect(screen.getByText(/加载校对队列失败/)).toBeInTheDocument()
      expect(screen.getByText(/网络超时/)).toBeInTheDocument()
    })
  })
})
