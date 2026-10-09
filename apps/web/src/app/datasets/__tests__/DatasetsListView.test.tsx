import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { DatasetsListView } from "../DatasetsListView"

// DatasetsListView uses next/link's router through the App Router context —
// stub it so component-level rendering works in jsdom.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}))

// ---------------------------------------------------------------------------
// Mock global.fetch: /api/datasets feeds the table.
// ---------------------------------------------------------------------------
const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

const LONG_TITLE = "UO2 - Lambertson and Handwerk, 1956"

const ITEM = {
  id: "ds-uo2-lambertson-handwerk-1956-0001",
  material_id: "mat-uo2-0001",
  material_name: "UO2",
  source_id: "src-lambertson-1956",
  source_title: "Lambertson & Handwerk — US AEC Report BMI-1240, 1956",
  title: LONG_TITLE,
  measurement_date: "1956-05-01",
  is_verified: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-09-01T08:00:00Z",
}

function datasetsResponse(): Response {
  return new Response(
    JSON.stringify({
      success: true,
      data: { items: [ITEM], total: 1, page: 1, limit: 20, pages: 1, truncated: false },
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  )
}

function routeAllDatasetsFetch(): void {
  mockFetch.mockImplementation((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.startsWith("/api/datasets")) {
      return Promise.resolve(datasetsResponse())
    }
    return Promise.reject(new Error(`unexpected fetch: ${url}`))
  })
}

beforeEach(() => {
  mockFetch.mockReset()
  routeAllDatasetsFetch()
})

function tableElement(): HTMLTableElement {
  const el = document.querySelector<HTMLTableElement>(".ant-table-content table")
  if (!el) throw new Error("antd table not rendered")
  return el
}

function contentElement(): HTMLElement {
  const el = document.querySelector<HTMLElement>(".ant-table-content")
  if (!el) throw new Error("antd scroll container not rendered")
  return el
}

// ---------------------------------------------------------------------------
// NFM-5330 regression: at 390px the fixed-width trailing columns (审核 96 /
// 测量日期 120 / 更新时间 120) rendered past the viewport edge with
// `overflow: visible` — unreachable, no scroll hint, 标题 wrapped to four
// lines. The structural contract of the fix (jsdom has no layout engine, so
// geometry is covered by the Playwright spec; this pins the DOM contract):
//   - antd's horizontal scroll container is engaged (overflow + fixed
//     840px min table width driving it)
//   - the three text columns are ellipsis cells with a title tooltip
//   - TableScrollFade wraps the table and (jsdom: no overflow computable)
//     renders no edge fades
// ---------------------------------------------------------------------------
describe("DatasetsListView table structure (NFM-5330)", () => {
  it("renders rows with the antd scroll container engaged", async () => {
    render(<DatasetsListView />)

    await waitFor(() => {
      expect(document.querySelectorAll(".ant-table-row")).toHaveLength(1)
    })
    expect(screen.getByText(LONG_TITLE)).toBeTruthy()
    expect(screen.getByText("未审核")).toBeTruthy()

    // scroll={{ x: 840 }} → rc-table marks .ant-table-content as the
    // overflow container and fixes the inner table's width at 840px
    // (min-width: 100% keeps desktop stretched — asserted in e2e).
    expect(contentElement().style.overflowX).toContain("auto")
    expect(tableElement().style.width).toBe("840px")
    expect(tableElement().style.minWidth).toBe("100%")
    // Fixed layout is what makes the ellipsis cells honour their widths
    // (auto layout re-widens nowrap cells) and pins the trailing fixed
    // columns (审核 96 / 测量日期 120 / 更新时间 120) via colgroup.
    expect(tableElement().style.tableLayout).toBe("fixed")
    const fixedCols = [
      ...contentElement().querySelectorAll("colgroup col[style]"),
    ].map((col) => (col as HTMLElement).style.width)
    expect(fixedCols).toEqual(["96px", "120px", "120px"])
  })

  it("renders the title cell as ellipsis with the full text as native tooltip", async () => {
    render(<DatasetsListView />)

    const titleCell = await waitFor(() => {
      const td = document.querySelector<HTMLElement>(".ant-table-tbody td")
      if (!td) throw new Error("no body cell yet")
      return td
    })
    expect(titleCell.className).toContain("ant-table-cell-ellipsis")
    // antd mirrors the cell value into the td title attribute so the
    // truncated title stays reachable as a native tooltip.
    expect(titleCell.getAttribute("title")).toBe(LONG_TITLE)
    // The anchor keeps its own tooltip too (材料 column convention).
    expect(titleCell.querySelector("a")?.getAttribute("title")).toBe(LONG_TITLE)
  })

  it("wraps the table in TableScrollFade, which renders no edge fades when nothing overflows", async () => {
    render(<DatasetsListView />)

    await waitFor(() => {
      expect(document.querySelectorAll(".ant-table-row")).toHaveLength(1)
    })
    expect(document.querySelector('[data-testid="table-scroll-fade"]')).not.toBeNull()
    // jsdom reports no overflow geometry, so both fades must stay hidden —
    // the same resting state as a desktop viewport in a real browser.
    expect(document.querySelector('[data-testid="table-scroll-fade-left"]')).toBeNull()
    expect(document.querySelector('[data-testid="table-scroll-fade-right"]')).toBeNull()
  })
})
