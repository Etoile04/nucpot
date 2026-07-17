import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { MaterialPropertyTable } from "../MaterialPropertyTable"
import type { MaterialProperty } from "@/lib/materials-api"

// ── Mock ConfidenceBadge ───────────────────────────────────────────────
// ConfidenceBadge uses dynamic Tailwind classes that don't resolve in jsdom.
// We mock it to a stable data-testid so tests can assert its presence.

vi.mock("@/components/shared/ConfidenceBadge", () => ({
  ConfidenceBadge: ({ value }: { value: number }) => (
    <span data-testid="confidence-badge" data-value={value} />
  ),
}))

// ── Test data ──────────────────────────────────────────────────────────

function makeProperty(overrides: Partial<MaterialProperty> = {}): MaterialProperty {
  return {
    id: "prop-001",
    name: "密度",
    value: "10.5",
    unit: "g/cm³",
    source: "文献A",
    confidence: 0.95,
    ...overrides,
  }
}

const SAMPLE_DATA: ReadonlyArray<MaterialProperty> = [
  makeProperty({ id: "p1", name: "密度", value: "10.5", unit: "g/cm³", source: "文献A", confidence: 0.95 }),
  makeProperty({ id: "p2", name: "熔点", value: "1850", unit: "K", source: "文献B", confidence: 0.80 }),
  makeProperty({ id: "p3", name: "热导率", value: "3.0", unit: "W/(m·K)", source: "文献C", confidence: 0.65 }),
]

const NULL_UNIT_PROP = makeProperty({
  id: "p4",
  name: "备注",
  value: "实验测定",
  unit: null,
  source: "实验",
  confidence: 0.50,
})

// ── Tests ─────────────────────────────────────────────────────────────

// Helper: the table gained pagination/sort/filter props after NFM-999.
// Most tests don't care about them, so we spread a sensible default bag.
const TABLE_PROPS = {
  page: 1,
  pageSize: 10,
  sortField: null,
  sortOrder: null,
  filterText: "",
  loading: false,
  onSortChange: () => {},
  onPageChange: () => {},
  onFilterChange: () => {},
} as const

describe("MaterialPropertyTable", () => {
  it("renders property rows with names, values, and units", () => {
    render(<MaterialPropertyTable {...TABLE_PROPS} data={SAMPLE_DATA} total={3} error={null} />)

    expect(screen.getByText("密度")).toBeInTheDocument()
    expect(screen.getByText("10.5")).toBeInTheDocument()
    expect(screen.getByText("g/cm³")).toBeInTheDocument()
  })

  it("renders source citations", () => {
    render(<MaterialPropertyTable {...TABLE_PROPS} data={SAMPLE_DATA} total={3} error={null} />)

    expect(screen.getByText("文献A")).toBeInTheDocument()
    expect(screen.getByText("文献B")).toBeInTheDocument()
  })

  it("renders confidence badges for each row", () => {
    render(<MaterialPropertyTable {...TABLE_PROPS} data={SAMPLE_DATA} total={3} error={null} />)

    const badges = screen.getAllByTestId("confidence-badge")
    expect(badges).toHaveLength(3)
    expect(badges[0]).toHaveAttribute("data-value", "0.95")
    expect(badges[1]).toHaveAttribute("data-value", "0.8")
    expect(badges[2]).toHaveAttribute("data-value", "0.65")
  })

  it("displays total count in header", () => {
    render(<MaterialPropertyTable {...TABLE_PROPS} data={SAMPLE_DATA} total={42} error={null} />)

    expect(screen.getByText(/共 42 条属性/)).toBeInTheDocument()
  })

  it("filters rows by search text across name and value", () => {
    render(<MaterialPropertyTable {...TABLE_PROPS} data={SAMPLE_DATA} total={3} error={null} />)

    const searchInput = screen.getByPlaceholderText("筛选属性...")
    fireEvent.change(searchInput, { target: { value: "密度" } })

    expect(screen.getByText("密度")).toBeInTheDocument()
    expect(screen.queryByText("熔点")).not.toBeInTheDocument()
    expect(screen.getByText(/筛选结果 1 条/)).toBeInTheDocument()
  })

  it("filters rows by source text", () => {
    render(<MaterialPropertyTable {...TABLE_PROPS} data={SAMPLE_DATA} total={3} error={null} />)

    const searchInput = screen.getByPlaceholderText("筛选属性...")
    fireEvent.change(searchInput, { target: { value: "文献C" } })

    expect(screen.getByText("热导率")).toBeInTheDocument()
    expect(screen.queryByText("密度")).not.toBeInTheDocument()
  })

  it("renders empty state when no data", () => {
    render(<MaterialPropertyTable {...TABLE_PROPS} data={[]} total={0} error={null} />)

    expect(screen.getByText("暂无属性数据")).toBeInTheDocument()
  })

  it("renders empty state when filter has no matches", () => {
    render(<MaterialPropertyTable {...TABLE_PROPS} data={SAMPLE_DATA} total={3} error={null} />)

    const searchInput = screen.getByPlaceholderText("筛选属性...")
    fireEvent.change(searchInput, { target: { value: "nonexistent" } })

    expect(screen.getByText("没有匹配的属性")).toBeInTheDocument()
  })

  it("renders error state with error message", () => {
    render(<MaterialPropertyTable {...TABLE_PROPS} data={[]} total={0} error="网络错误" />)

    expect(screen.getByText(/加载失败：网络错误/)).toBeInTheDocument()
  })

  it("renders loading spinner when loading is true", () => {
    const { container } = render(
      <MaterialPropertyTable {...TABLE_PROPS} data={[]} total={0} loading error={null} />,
    )

    expect(container.querySelector(".ant-spin")).toBeTruthy()
  })

  it("renders '—' for null unit", () => {
    render(<MaterialPropertyTable {...TABLE_PROPS} data={[NULL_UNIT_PROP]} total={1} error={null} />)

    const dashes = screen.getAllByText("—")
    expect(dashes.length).toBeGreaterThanOrEqual(1)
  })

  it("does not show filter count when no filter is active", () => {
    render(<MaterialPropertyTable {...TABLE_PROPS} data={SAMPLE_DATA} total={3} error={null} />)

    expect(screen.queryByText(/筛选结果/)).not.toBeInTheDocument()
  })

  it("clears filter when search input is cleared", () => {
    render(<MaterialPropertyTable {...TABLE_PROPS} data={SAMPLE_DATA} total={3} error={null} />)

    const searchInput = screen.getByPlaceholderText("筛选属性...") as HTMLInputElement
    fireEvent.change(searchInput, { target: { value: "密度" } })
    expect(screen.queryByText("熔点")).not.toBeInTheDocument()

    fireEvent.change(searchInput, { target: { value: "" } })
    expect(screen.getByText("熔点")).toBeInTheDocument()
    expect(screen.queryByText(/筛选结果/)).not.toBeInTheDocument()
  })
})
