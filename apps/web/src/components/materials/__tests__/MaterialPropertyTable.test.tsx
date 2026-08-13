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

// Helper: the table became a controlled component after NFM-999 — the parent
// owns sort/filter/pagination state and re-fetches from the server.  Tests
// therefore (a) pre-filter the data they pass in, and (b) assert that user
// input in the filter box invokes `onFilterChange` rather than mutating the
// table in place.
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

  it("invokes onFilterChange when the user types in the filter box (controlled)", () => {
    const onFilterChange = vi.fn()
    render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        onFilterChange={onFilterChange}
        data={SAMPLE_DATA}
        total={3}
        error={null}
      />,
    )

    const searchInput = screen.getByPlaceholderText("筛选属性...")
    fireEvent.change(searchInput, { target: { value: "密度" } })

    expect(onFilterChange).toHaveBeenCalledTimes(1)
    expect(onFilterChange).toHaveBeenCalledWith("密度")
  })

  it("renders only the parent-supplied (pre-filtered) rows for a name filter", () => {
    // Parent has already filtered to rows matching "密度".
    const filtered = SAMPLE_DATA.filter((p) => p.name.includes("密度"))
    render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        filterText="密度"
        data={filtered}
        total={filtered.length}
        error={null}
      />,
    )

    expect(screen.getByText("密度")).toBeInTheDocument()
    expect(screen.queryByText("熔点")).not.toBeInTheDocument()
  })

  it("renders only the parent-supplied (pre-filtered) rows for a source filter", () => {
    // Parent has already filtered to rows whose source matches "文献C".
    const filtered = SAMPLE_DATA.filter((p) => p.source.includes("文献C"))
    render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        filterText="文献C"
        data={filtered}
        total={filtered.length}
        error={null}
      />,
    )

    expect(screen.getByText("热导率")).toBeInTheDocument()
    expect(screen.queryByText("密度")).not.toBeInTheDocument()
  })

  it("renders empty state when no data", () => {
    render(<MaterialPropertyTable {...TABLE_PROPS} data={[]} total={0} error={null} />)

    expect(screen.getByText("暂无属性数据")).toBeInTheDocument()
  })

  it("renders 'no matches' empty state when filterText is set but data is empty", () => {
    // Controlled component: a non-empty filterText with zero rows signals
    // the parent's filter produced no matches → show the filtered empty copy.
    render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        filterText="nonexistent"
        data={[]}
        total={0}
        error={null}
      />,
    )

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

  it("reflects controlled filterText in the input", () => {
    render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        filterText="密度"
        data={SAMPLE_DATA}
        total={3}
        error={null}
      />,
    )

    const searchInput = screen.getByPlaceholderText("筛选属性...") as HTMLInputElement
    expect(searchInput.value).toBe("密度")
  })

  it("clears the displayed filter when the parent resets filterText to empty", () => {
    const { rerender } = render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        filterText="密度"
        data={SAMPLE_DATA.filter((p) => p.name.includes("密度"))}
        total={1}
        error={null}
      />,
    )

    const searchInput = screen.getByPlaceholderText("筛选属性...") as HTMLInputElement
    expect(searchInput.value).toBe("密度")

    // Parent clears the filter and restores the full dataset.
    rerender(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        filterText=""
        data={SAMPLE_DATA}
        total={3}
        error={null}
      />,
    )

    const searchInputAfter = screen.getByPlaceholderText("筛选属性...") as HTMLInputElement
    expect(searchInputAfter.value).toBe("")
    expect(screen.getByText("熔点")).toBeInTheDocument()
  })
})
