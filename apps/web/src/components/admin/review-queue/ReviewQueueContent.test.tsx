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
  unitId: "unit-W-p-K",
  unitSymbol: "W/(m·K)",
  notes: null,
  propertyTypeId: "pt-lattice",
  propertyTypeName: "lattice parameter a",
  datasetId: null,
  dedupeKey: null,
  validityCheck: { status: "unknown" as const, reason: null },
}

const highRow = {
  id: "row-high",
  itemType: "measurement" as const,
  confidence: 0.95,
  reviewStatus: "pending",
  source: { paragraph: "high confidence paragraph", page: 2, doi: null },
  createdAt: "2026-09-10T00:00:00Z",
  valueScalar: 1.23,
  unitId: "unit-g-cm3",
  unitSymbol: "g/cm³",
  notes: null,
  propertyTypeId: "pt-density",
  propertyTypeName: "density",
  datasetId: null,
  dedupeKey: null,
  validityCheck: { status: "unknown" as const, reason: null },
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
      // Antd v5 emits a virtual-scroll measure cell mirroring each
      // header, so headers appear twice. Use getAllByText and assert
      // presence + a structural count of 6 distinct <th scope="col">.
      const headers = Array.from(
        document.querySelectorAll<HTMLElement>("th.ant-table-cell[scope='col']"),
      ).map((el) => el.textContent?.trim() ?? "")
      expect(headers).toEqual(
        expect.arrayContaining(["属性", "值", "单位", "置信度", "来源段落", "状态"]),
      )
      expect(headers).toHaveLength(6)
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

  // NFM-4554 G1-F UXDesigner Visual-Truth Gate bounce-back fix #1:
  // 属性 column MUST show propertyTypeName, NOT a row UUID prefix.
  it("renders property name (not row UUID) in the 属性 column", async () => {
    mockedFetch.mockResolvedValueOnce({
      items: [lowRow, highRow],
      total: 2,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      const names = screen.getAllByTestId("property-name")
      expect(names[0]).toHaveTextContent("lattice parameter a")
      expect(names[1]).toHaveTextContent("density")
      // Sanity: no row UUID prefix anywhere in the property column
      expect(screen.queryByText(/^row-001-/)).not.toBeInTheDocument()
      expect(screen.queryByText("row-low")).not.toBeInTheDocument()
    })
  })

  it("falls back to a short id prefix when propertyTypeName is missing (legacy rows)", async () => {
    const legacyRow = {
      ...lowRow,
      id: "legacy-uuid-12345678",
      propertyTypeName: null,
    }
    mockedFetch.mockResolvedValueOnce({
      items: [legacyRow],
      total: 1,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      const name = screen.getByTestId("property-name")
      expect(name.textContent).toContain("legacy-u")
    })
  })

  // NFM-4560 — UXDesigner Visual-Truth Gate round-3 follow-up:
  // 单位 column must show the resolved unit symbol (e.g. "W/(m·K)",
  // "g/cm³"), NOT a row UUID prefix.
  it("renders the unit symbol (not the unit_id prefix) in the 单位 column", async () => {
    mockedFetch.mockResolvedValueOnce({
      items: [lowRow, highRow],
      total: 2,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      const symbols = screen.getAllByTestId("unit-symbol")
      expect(symbols).toHaveLength(2)
      expect(symbols[0]).toHaveTextContent("W/(m·K)")
      expect(symbols[1]).toHaveTextContent("g/cm³")
      // Sanity: no row UUID prefix in any unit cell
      expect(screen.queryByText(/^unit-W-p-K$/)).not.toBeInTheDocument()
      expect(screen.queryByText(/^unit-g-cm3$/)).not.toBeInTheDocument()
    })
  })

  it("falls back to a short unit_id prefix when unitSymbol is missing (legacy rows)", async () => {
    const legacyRow = {
      ...lowRow,
      unitSymbol: null,
      unitId: "unit-legacy-12345678-abcdef",
    }
    mockedFetch.mockResolvedValueOnce({
      items: [legacyRow],
      total: 1,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      // The fallback shows the first 8 chars of unitId
      const fallback = screen.getByTestId("unit-fallback")
      expect(fallback.textContent).toContain("unit-leg")
    })
    // The resolved-symbol path is NOT taken
    expect(screen.queryByTestId("unit-symbol")).not.toBeInTheDocument()
  })

  it("renders a dash placeholder when neither unitSymbol nor unitId is set", async () => {
    const noUnitRow = {
      ...lowRow,
      unitSymbol: null,
      unitId: null,
    }
    mockedFetch.mockResolvedValueOnce({
      items: [noUnitRow],
      total: 1,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      expect(screen.getByTestId("unit-empty")).toBeInTheDocument()
    })
    // Neither symbol nor fallback path is taken
    expect(screen.queryByTestId("unit-symbol")).not.toBeInTheDocument()
    expect(screen.queryByTestId("unit-fallback")).not.toBeInTheDocument()
  })

  // NFM-4554 G1-F UXDesigner Visual-Truth Gate bounce-back fix #2:
  // spec §4.3 dedupe_key badge ("已合并 N 行") when multiple rows share
  // the same key.
  it("renders the dedupe_key badge when ≥2 rows share the same key", async () => {
    const merged1 = { ...lowRow, id: "row-A", dedupeKey: "dedupe-XYZ" }
    const merged2 = { ...lowRow, id: "row-B", dedupeKey: "dedupe-XYZ" }
    const merged3 = { ...lowRow, id: "row-C", dedupeKey: "dedupe-XYZ" }
    mockedFetch.mockResolvedValueOnce({
      items: [merged1, merged2, merged3],
      total: 3,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      const badges = screen.getAllByTestId("dedupe-merged-badge")
      expect(badges).toHaveLength(3)
      for (const badge of badges) {
        expect(badge).toHaveTextContent("已合并 3 行")
      }
    })
  })

  it("does NOT render the dedupe_key badge when only one row carries the key", async () => {
    const soloRow = { ...lowRow, dedupeKey: "dedupe-solo" }
    mockedFetch.mockResolvedValueOnce({
      items: [soloRow],
      total: 1,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      expect(screen.getByText("lattice parameter a")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("dedupe-merged-badge")).not.toBeInTheDocument()
  })

  // NFM-4554 G1-F UXDesigner Visual-Truth Gate bounce-back fix #3:
  // spec §4.3 红行 (physical-invalid row treatment) at the table level,
  // not just inside the drawer. Dormant until G1-D (NFM-4550) ships the
  // validity_check column on property_measurements.
  it("applies the physically-invalid row class + reason tooltip when validity_check.status='fail'", async () => {
    const invalidRow = {
      ...lowRow,
      id: "row-invalid",
      validityCheck: {
        status: "fail" as const,
        reason: "lattice parameter a < 0.3 Å — outside valid range",
      },
    }
    mockedFetch.mockResolvedValueOnce({
      items: [invalidRow],
      total: 1,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      const tr = document.querySelector("tr.review-row-physically-invalid")
      expect(tr).toBeInTheDocument()
      expect(tr).toHaveAttribute("title", invalidRow.validityCheck.reason)
    })
  })

  it("does NOT mark a row invalid when validity_check.status is 'unknown'", async () => {
    mockedFetch.mockResolvedValueOnce({
      items: [lowRow],
      total: 1,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      expect(screen.getByText("lattice parameter a")).toBeInTheDocument()
    })
    expect(
      document.querySelector("tr.review-row-physically-invalid"),
    ).not.toBeInTheDocument()
  })

  // NFM-4554 G1-F UXDesigner Visual-Truth Gate bounce-back fix #4:
  // mobile (≤ xs) — all six spec §4.2 columns must remain reachable,
  // not collapsed past the viewport edge.
  it("wraps the table in a horizontal scroll container so all 6 columns stay reachable on narrow viewports", async () => {
    mockedFetch.mockResolvedValueOnce({
      items: [lowRow, highRow],
      total: 2,
      page: 1,
      pages: 1,
    })
    renderWithQuery(<ReviewQueueContent />)
    await waitFor(() => {
      expect(screen.getByTestId("review-queue-table-scroll")).toBeInTheDocument()
    })
    const scrollWrap = screen.getByTestId("review-queue-table-scroll")
    const style = window.getComputedStyle(scrollWrap)
    expect(style.overflowX).toBe("auto")
    // The Table itself declares a min-width scroll target so the
    // scroll surface has something to scroll horizontally.
    const table = scrollWrap.querySelector(".ant-table")
    expect(table).toBeInTheDocument()
  })
})
