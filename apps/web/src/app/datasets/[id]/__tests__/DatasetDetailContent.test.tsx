import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { DatasetDetailContent } from "../DatasetDetailContent"
import type { DatasetDetail } from "@/lib/datasets-server"

// NFM-5321 hotfix regression guard: the antd subtree of the /datasets
// detail page must render from a client component. The original
// Server-Component version SSR-crashed in production (antd Typography
// statics resolve to undefined in the server runtime → `Element type
// is invalid` → HTTP 500 on every /datasets/{id}); this suite pins the
// D2 behaviours the page must keep after the extraction.

const CREATED_AT = "2026-10-05T12:00:00.000000Z"

function makeDataset(overrides: Partial<DatasetDetail> = {}): DatasetDetail {
  return {
    id: "62962f1b-8fb1-47d6-b480-b3a13b7fdbc4",
    material_id: "068dc946-9dd9-4a8d-bad0-9f24359b8b87",
    source_id: "6713a308-c4e6-4d36-9d6b-46ee484d21e1",
    title: "UO2 - FSWELL correlation (MATPRO)",
    description: null,
    measurement_date: null,
    is_verified: false,
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    attribution: { status: "intact" },
    material_name: "UO2",
    source_title: "FSWELL correlation (MATPRO)",
    ...overrides,
  }
}

/** Local-date expectation, derived the same way formatDate derives it. */
function expectedLocalDate(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

describe("DatasetDetailContent", () => {
  it("renders the dataset title and resolved material/source names (D2)", () => {
    render(<DatasetDetailContent dataset={makeDataset()} />)
    expect(screen.getByText("UO2 - FSWELL correlation (MATPRO)")).toBeTruthy()
    // Material + source render as links carrying the resolved names.
    expect(screen.getByRole("link", { name: "UO2" }).getAttribute("href")).toBe(
      "/materials/068dc946-9dd9-4a8d-bad0-9f24359b8b87",
    )
    expect(
      screen
        .getByRole("link", { name: "FSWELL correlation (MATPRO)" })
        .getAttribute("href"),
    ).toBe("/publications/6713a308-c4e6-4d36-9d6b-46ee484d21e1")
  })

  it("renders attribution as 中文标签, never the raw enum (D2)", () => {
    const { rerender } = render(<DatasetDetailContent dataset={makeDataset()} />)
    expect(screen.getByText("归属完整")).toBeTruthy()
    expect(screen.queryByText("intact")).toBeNull()

    rerender(
      <DatasetDetailContent
        dataset={makeDataset({ attribution: { status: "placeholder" } })}
      />,
    )
    expect(screen.getByText("归属占位")).toBeTruthy()
    expect(screen.getByText("占位数据集")).toBeTruthy()
    expect(screen.queryByText("placeholder")).toBeNull()
  })

  it("renders created/updated as local YYYY-MM-DD, not ISO 8601 (D2)", () => {
    render(<DatasetDetailContent dataset={makeDataset()} />)
    const expected = expectedLocalDate(CREATED_AT)
    expect(screen.getAllByText(expected).length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText(CREATED_AT)).toBeNull()
  })

  it("falls back to 「—」 for unresolved names and 「无」 for a missing source (D2)", () => {
    render(
      <DatasetDetailContent
        dataset={makeDataset({
          material_name: null,
          source_title: null,
          source_id: null,
        })}
      />,
    )
    // Unresolved material renders the em-dash fallback; a null source
    // and a null description each render 无 (the table's empty-value
    // conventions).
    expect(screen.getByText("—")).toBeTruthy()
    expect(screen.getAllByText("无").length).toBe(2)
  })
})
