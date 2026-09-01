import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import {
  MaterialPropertyTable,
  formatCitation,
  groupKey,
  groupRowsByKey,
  renderSourceCell,
} from "../MaterialPropertyTable"
import type { MaterialProperty, MeasurementCondition, SourceRef } from "@/lib/materials-api"

// ── Mock ConfidenceBadge ───────────────────────────────────────────────
// ConfidenceBadge uses dynamic Tailwind classes that don't resolve in jsdom.
// We mock it to a stable data-testid so tests can assert its presence.

vi.mock("@/components/shared/ConfidenceBadge", () => ({
  ConfidenceBadge: ({ value }: { value: number }) => (
    <span data-testid="confidence-badge" data-value={value} />
  ),
}))

// ── Test data ──────────────────────────────────────────────────────────

function makeSource(overrides: Partial<SourceRef> = {}): SourceRef {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    title: "Default title",
    doi: null,
    journal: null,
    year: null,
    authors: [],
    url: null,
    ...overrides,
  }
}

function makeProperty(overrides: Partial<MaterialProperty> = {}): MaterialProperty {
  return {
    id: "prop-001",
    name: "密度",
    value: "10.5",
    unit: "g/cm³",
    source: makeSource({ title: "文献A" }),
    confidence: 0.95,
    conditions: [],
    ...overrides,
  }
}

function makeCondition(
  overrides: Partial<MeasurementCondition> = {},
): MeasurementCondition {
  return {
    id: "cond-001",
    measurement_id: "prop-001",
    temperature: 298.15,
    pressure: null,
    environment: null,
    irradiation_dose: null,
    notes: null,
    ...overrides,
  }
}

const SAMPLE_DATA: ReadonlyArray<MaterialProperty> = [
  makeProperty({
    id: "p1",
    name: "密度",
    value: "10.5",
    unit: "g/cm³",
    source: makeSource({ title: "文献A" }),
    confidence: 0.95,
  }),
  makeProperty({
    id: "p2",
    name: "熔点",
    value: "1850",
    unit: "K",
    source: makeSource({ title: "文献B" }),
    confidence: 0.80,
  }),
  makeProperty({
    id: "p3",
    name: "热导率",
    value: "3.0",
    unit: "W/(m·K)",
    source: makeSource({ title: "文献C" }),
    confidence: 0.65,
  }),
]

const NULL_UNIT_PROP = makeProperty({
  id: "p4",
  name: "备注",
  value: "实验测定",
  unit: null,
  source: makeSource({ title: "实验" }),
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

    // With no authors/year/journal, the citation falls back to the bare
    // title — the same text the legacy code rendered, so existing
    // curators see no visual change for un-enriched sources.
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
    // Parent has already filtered to rows whose source title contains
    // "文献C". The `source` field is now an object, so the parent's
    // pre-filter operates on `source.title`.
    const filtered = SAMPLE_DATA.filter((p) => p.source?.title.includes("文献C"))
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

  // ── NFM-4085 (B): sticky header + inner scroll when many rows ──────
  //
  // The acceptance criterion is "属性表 >20 行 mock 数据下表头 sticky、容器内滚".
  // antd Table implements sticky headers + inner scroll via the `scroll.y`
  // prop, which sets a `max-height` style on the `.ant-table-body` element.
  // We assert this style so a regression that drops the scroll config (and
  // regresses the inner-scroll behavior) is caught by the suite.

  it("enables inner-scroll (scroll.y) when more than 20 rows are present", () => {
    const manyRows: ReadonlyArray<MaterialProperty> = Array.from(
      { length: 25 },
      (_, i) =>
        makeProperty({
          id: `p-many-${i + 1}`,
          name: `属性 ${i + 1}`,
          value: String(i + 1),
          unit: "g/cm³",
          source: makeSource({ title: `文献${i + 1}` }),
          confidence: 0.5 + (i % 5) * 0.1,
        }),
    )
    const { container } = render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={manyRows}
        total={manyRows.length}
        error={null}
      />,
    )

    // antd exposes the scrollable body via `.ant-table-body` ONLY when
    // `scroll.y` (or `scroll.x`) is set — without it, the body is laid
    // out in `.ant-table-tbody` directly. The presence of `.ant-table-body`
    // is therefore the strongest signal that the scroll container engaged.
    // Read the inline `max-height` style (jsdom does not compute styles,
    // but antd writes the prop as `style={{ maxHeight: ... }}`).
    const scrollBody = container.querySelector(".ant-table-body")
    expect(scrollBody).not.toBeNull()
    const maxHeight = (scrollBody as HTMLElement).style.maxHeight
    expect(maxHeight).toBeTruthy()
    expect(maxHeight).not.toBe("none")
  })

  it("does NOT enable inner-scroll when 20 rows or fewer are present (threshold = 20)", () => {
    const fewRows: ReadonlyArray<MaterialProperty> = Array.from(
      { length: 7 },
      (_, i) =>
        makeProperty({
          id: `p-few-${i + 1}`,
          name: `属性 ${i + 1}`,
          value: String(i + 1),
          unit: "g/cm³",
          source: makeSource({ title: `文献${i + 1}` }),
          confidence: 0.5,
        }),
    )
    const { container } = render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={fewRows}
        total={fewRows.length}
        error={null}
      />,
    )

    // Without scroll.y, antd renders the rows directly inside
    // `.ant-table-tbody` and does NOT wrap them in `.ant-table-body`.
    // The threshold (20) was chosen so the existing ≤7-row baseline is
    // unaffected (matches the issue's "目前 ≤7 未触及痛点" note).
    const scrollBody = container.querySelector(".ant-table-body")
    expect(scrollBody).toBeNull()
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

// ── NFM-4086 — D1 来源可读化: structured source rendering ───────────────

describe("MaterialPropertyTable — NFM-4086 source enrichment", () => {
  it("renders enriched source with DOI link", () => {
    // Fully-populated SourceRef — author/year/journal/doi/url all set.
    // The column should render the abbreviated citation "Owen, L.,
    // Patel, R. (2023). J. Nucl. Mater." wrapped in an <a> that points
    // at the DOI URL with the standard rel attrs.
    const enriched: MaterialProperty = makeProperty({
      id: "p-enriched",
      name: "热导率",
      value: "3.0",
      unit: "W/(m·K)",
      source: makeSource({
        id: "11111111-1111-1111-1111-111111111111",
        title: "Thermal conductivity of UO2 revisited",
        doi: "10.1016/j.jnucmat.2023.123456",
        journal: "J. Nucl. Mater.",
        year: 2023,
        authors: ["Owen, L.", "Patel, R."],
        url: "https://doi.org/10.1016/j.jnucmat.2023.123456",
      }),
      confidence: 0.95,
    })

    render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={[enriched]}
        total={1}
        error={null}
      />,
    )

    const link = screen.getByRole("link", { name: /Owen, L\., Patel, R\. \(2023\)\. J\. Nucl\. Mater\./ })
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute("href", "https://doi.org/10.1016/j.jnucmat.2023.123456")
    expect(link).toHaveAttribute("target", "_blank")
    expect(link).toHaveAttribute("rel", "noopener noreferrer")
  })

  it("renders 'Unsourced' when source=null", () => {
    // When the backend returns a row whose source is null (no attached
    // DataSource), the citation column falls back to the literal
    // "Unsourced" — same copy as the legacy code, so existing UX stays
    // intact for orphan datasets.
    const orphan: MaterialProperty = makeProperty({
      id: "p-orphan",
      name: "密度",
      value: "5.68",
      unit: "g/cm³",
      source: null,
      confidence: 0.5,
    })

    render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={[orphan]}
        total={1}
        error={null}
      />,
    )

    expect(screen.getByText("Unsourced")).toBeInTheDocument()
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
  })

  it("escapes special characters in title (XSS-safe)", () => {
    // Curators can paste titles containing HTML-significant characters.
    // React's default text rendering escapes them at the DOM boundary
    // so the page never injects raw HTML — we assert that no <script>
    // or <img> elements are parsed from user-supplied strings, and the
    // anchor's `href` is exactly the URL we passed (no HTML injection
    // through the URL either).
    const xss: MaterialProperty = makeProperty({
      id: "p-xss",
      name: "密度",
      value: "5.68",
      unit: "g/cm³",
      source: makeSource({
        id: "22222222-2222-2222-2222-222222222222",
        title: "<script>alert('xss')</script>",
        doi: "10.1/foo",
        journal: "<img src=x onerror=alert(1)>",
        year: 2023,
        authors: ["Owen, L."],
        url: "https://doi.org/10.1/foo",
      }),
      confidence: 0.5,
    })

    const { container } = render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={[xss]}
        total={1}
        error={null}
      />,
    )

    // No actual <script> tag and no <img onerror> tag were injected
    // from user-supplied strings — query the rendered DOM directly so
    // we catch any escape failure.
    expect(container.querySelectorAll("script").length).toBe(0)
    expect(container.querySelectorAll("img[onerror]").length).toBe(0)

    // The visible citation text contains the dangerous payload — but
    // it's text content, not parsed HTML. Browsers display the literal
    // "<img …>" string; the DOM tree never contains an <img> child.
    const anchor = screen.getByRole("link")
    expect(anchor.textContent).toContain("Owen, L.")
    expect(anchor.textContent).toContain("<img src=x onerror=alert(1)>")

    // The link href is exactly what the backend sent — not whatever
    // might have leaked from a malicious authors entry.
    expect(anchor.getAttribute("href")).toBe("https://doi.org/10.1/foo")
  })
})

// ── formatCitation unit tests ──────────────────────────────────────────

describe("formatCitation", () => {
  it("renders Authors (Year). Journal. when all fields are populated", () => {
    const ref = makeSource({
      authors: ["Owen, L.", "Patel, R."],
      year: 2023,
      journal: "J. Nucl. Mater.",
      title: "ignored",
    })
    expect(formatCitation(ref)).toBe("Owen, L., Patel, R. (2023). J. Nucl. Mater.")
  })

  it("falls back to the bare title when nothing else is populated", () => {
    expect(formatCitation(makeSource({ title: "Solo Paper" }))).toBe("Solo Paper")
  })

  it("renders partial forms when only some fields are present", () => {
    expect(formatCitation(makeSource({ authors: ["Owen, L."], title: "t" }))).toBe(
      "Owen, L.",
    )
    expect(formatCitation(makeSource({ year: 2020, title: "t" }))).toBe("(2020)")
  })
})

// Suppress unused-import lint when `renderSourceCell` is only used in
// downstream consumers; keep the export verified with a smoke test.
describe("renderSourceCell", () => {
  it("returns the Unsourced text node when source is null", () => {
    const node = renderSourceCell(null)
    expect(node).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// NFM-4087 — D2 duplicate-row grouping
// ---------------------------------------------------------------------------

describe("groupKey", () => {
  it("returns identical keys for rows that share (name, value, source.id)", () => {
    const a = makeProperty({ id: "p1" })
    const b = makeProperty({ id: "p2" })
    expect(groupKey(a)).toBe(groupKey(b))
  })

  it("differs when source.id changes even if name + value match", () => {
    const a = makeProperty({
      name: "Activation Energy",
      value: "0.3",
      source: makeSource({ id: "00000000-0000-0000-0000-000000000001" }),
    })
    const b = makeProperty({
      name: "Activation Energy",
      value: "0.3",
      source: makeSource({ id: "00000000-0000-0000-0000-000000000002" }),
    })
    expect(groupKey(a)).not.toBe(groupKey(b))
  })

  it("groups unsourced rows together (sentinel)", () => {
    const a = makeProperty({ source: null })
    const b = makeProperty({ source: null, id: "p2" })
    expect(groupKey(a)).toBe(groupKey(b))
  })
})

describe("groupRowsByKey", () => {
  it("folds 4 rows of UO2 activation_energy into 1 group with count=4", () => {
    // Reproduces the UO2 activation_energy=0.3 eV scenario described in
    // the NFM-4084 / NFM-4087 backstory.
    const source = makeSource({
      id: "9320cb50-aaaa-bbbb-cccc-000000000001",
      title: "UO2 Activation Energy (Hall 1989)",
    })
    const rows: ReadonlyArray<MaterialProperty> = [
      makeProperty({
        id: "ae-1",
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source,
        conditions: [
          makeCondition({
            id: "c-1",
            measurement_id: "ae-1",
            temperature: 873.15,
            pressure: 0.1,
            environment: "inert",
          }),
        ],
      }),
      makeProperty({
        id: "ae-2",
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source,
        conditions: [
          makeCondition({
            id: "c-2",
            measurement_id: "ae-2",
            temperature: 1073.15,
            pressure: 0.1,
            environment: "inert",
          }),
        ],
      }),
      makeProperty({
        id: "ae-3",
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source,
        conditions: [
          makeCondition({
            id: "c-3",
            measurement_id: "ae-3",
            temperature: 1273.15,
            pressure: 0.1,
            environment: "inert",
          }),
        ],
      }),
      makeProperty({
        id: "ae-4",
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source,
        conditions: [
          makeCondition({
            id: "c-4",
            measurement_id: "ae-4",
            temperature: 1473.15,
            pressure: 0.1,
            environment: "inert",
          }),
        ],
      }),
    ]

    const grouped = groupRowsByKey(rows)

    expect(grouped).toHaveLength(1)
    expect(grouped[0]?.count).toBe(4)
    expect(grouped[0]?.allMeasurements.map((m) => m.id).sort()).toEqual([
      "ae-1",
      "ae-2",
      "ae-3",
      "ae-4",
    ])
  })

  it("does NOT group rows that differ on source.id even if title matches", () => {
    // Regression guard: NFM-4084 F2 — two papers sharing a short title
    // prefix ("Owen (2023). J. Nucl. Mater.") must not fold together
    // when they reference distinct sources. Using `source.id` (stable
    // UUID) instead of `source.title` is the whole point of D1's
    // SourceRef upgrade; this test pins that contract.
    const rows: ReadonlyArray<MaterialProperty> = [
      makeProperty({
        id: "p1",
        name: "Density",
        value: "10.5",
        source: makeSource({
          id: "00000000-0000-0000-0000-000000000001",
          title: "Same title",
        }),
      }),
      makeProperty({
        id: "p2",
        name: "Density",
        value: "10.5",
        source: makeSource({
          id: "00000000-0000-0000-0000-000000000002",
          title: "Same title",
        }),
      }),
    ]

    const grouped = groupRowsByKey(rows)

    expect(grouped).toHaveLength(2)
    expect(grouped[0]?.count).toBe(1)
    expect(grouped[1]?.count).toBe(1)
  })

  it("preserves the original first-appearance order across the page", () => {
    // The backend returns rows in a deterministic order (sort by name
    // asc). The grouping helper must NOT re-sort — that would scramble
    // adjacent duplicates and confuse the sort indicator.
    const source = makeSource({ id: "00000000-0000-0000-0000-000000000003" })
    const rows: ReadonlyArray<MaterialProperty> = [
      makeProperty({ id: "a", name: "Alpha", value: "1", source }),
      makeProperty({ id: "b", name: "Beta", value: "2", source }),
      makeProperty({ id: "a2", name: "Alpha", value: "1", source }),
    ]

    const grouped = groupRowsByKey(rows)

    expect(grouped.map((g) => g.key)).toEqual(["a", "b"])
    expect(grouped[0]?.allMeasurements.map((m) => m.id)).toEqual(["a", "a2"])
  })
})

describe("MaterialPropertyTable — NFM-4087 grouped rendering", () => {
  it("renders a single fold row with ×N count badge when 4 measurements share key", () => {
    const source = makeSource({ title: "Folded source" })
    const rows: ReadonlyArray<MaterialProperty> = [1, 2, 3, 4].map((i) =>
      makeProperty({
        id: `ae-${i}`,
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source,
        confidence: 0.9,
        conditions: [
          makeCondition({
            id: `c-${i}`,
            measurement_id: `ae-${i}`,
            temperature: 298.15 + i * 100,
            pressure: 0.1,
            environment: "inert",
          }),
        ],
      }),
    )

    render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={rows}
        total={rows.length}
        error={null}
      />,
    )

    // Exactly one row of attribute name "Activation Energy" is rendered,
    // not four. The CountBadge carries the "×4" suffix.
    expect(screen.getAllByText("Activation Energy")).toHaveLength(1)
    expect(screen.getByText("×4")).toBeInTheDocument()
  })

  it("does NOT fold rows whose source.id differs even when the title matches", () => {
    const sharedTitle = "Same citation title"
    const rows: ReadonlyArray<MaterialProperty> = [
      makeProperty({
        id: "src1",
        name: "Density",
        value: "10.5",
        source: makeSource({ id: "src-aaa", title: sharedTitle }),
      }),
      makeProperty({
        id: "src2",
        name: "Density",
        value: "10.5",
        source: makeSource({ id: "src-bbb", title: sharedTitle }),
      }),
    ]

    render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={rows}
        total={rows.length}
        error={null}
      />,
    )

    // Both rows render separately; no count badge because neither group
    // has count > 1.
    expect(screen.getAllByText("Density")).toHaveLength(2)
    expect(screen.queryByText(/^×/)).not.toBeInTheDocument()
  })

  it("expander reveals temperature / pressure / environment for each underlying measurement", () => {
    const source = makeSource({ title: "Multi-condition source" })
    const rows: ReadonlyArray<MaterialProperty> = [
      makeProperty({
        id: "ae-1",
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source,
        conditions: [
          makeCondition({
            id: "c-1",
            measurement_id: "ae-1",
            temperature: 873.15,
            pressure: 0.1,
            environment: "inert",
          }),
        ],
      }),
      makeProperty({
        id: "ae-2",
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source,
        conditions: [
          makeCondition({
            id: "c-2",
            measurement_id: "ae-2",
            temperature: 1073.15,
            pressure: 10.0,
            environment: "oxidising",
            notes: "post-anneal",
          }),
        ],
      }),
    ]

    render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={rows}
        total={rows.length}
        error={null}
      />,
    )

    // The single grouped row has an expand affordance labelled
    // "展开 conditions"; click it to open the sub-table.
    const expandButton = screen.getByRole("button", { name: "展开 conditions" })
    fireEvent.click(expandButton)

    // The expander table shows the heading "底层 2 条 measurement 的 conditions".
    expect(
      screen.getByText("底层 2 条 measurement 的 conditions"),
    ).toBeInTheDocument()

    // Each condition row carries its temperature / pressure / environment.
    expect(screen.getByText("873.15 K")).toBeInTheDocument()
    expect(screen.getByText("0.10 MPa")).toBeInTheDocument()
    expect(screen.getByText("1073.15 K")).toBeInTheDocument()
    expect(screen.getByText("10.00 MPa")).toBeInTheDocument()
    expect(screen.getByText("oxidising")).toBeInTheDocument()
    expect(screen.getByText("post-anneal")).toBeInTheDocument()
  })

  it("shows the page-level fold count next to the total when the page folded", () => {
    const source = makeSource({ title: "Folded source" })
    const rows: ReadonlyArray<MaterialProperty> = [1, 2].map((i) =>
      makeProperty({
        id: `dup-${i}`,
        name: "Density",
        value: "10.5",
        source,
      }),
    )

    render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={rows}
        total={rows.length}
        error={null}
      />,
    )

    // Header reads "共 2 条属性" (raw total) plus "(本页折叠为 1 行)" hint.
    expect(screen.getByText(/共 2 条属性/)).toBeInTheDocument()
    expect(screen.getByText("(本页折叠为 1 行)")).toBeInTheDocument()
  })

  it("omits the fold-count hint when the page is fully unfolded", () => {
    const rows: ReadonlyArray<MaterialProperty> = [
      makeProperty({ id: "p1", name: "Density", value: "10.5" }),
      makeProperty({ id: "p2", name: "Melting", value: "1850" }),
    ]

    render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={rows}
        total={rows.length}
        error={null}
      />,
    )

    expect(screen.getByText(/共 2 条属性/)).toBeInTheDocument()
    expect(screen.queryByText(/本页折叠为/)).not.toBeInTheDocument()
  })
})
