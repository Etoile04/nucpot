import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import {
  MaterialPropertyTable,
  formatCitation,
  groupKey,
  groupRowsByKey,
  renderSourceCell,
  rowAttributionStatus,
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
//
// Group key: (name, value, source?.title).
//
// Why title and not id: pre-NFM-4088 the `data_sources` table holds
// UUID-title duplicates — different UUIDs representing the same paper.
// Keying on id in that state produced multiple buckets for one logical
// paper. Title-keyed grouping matches the user mental model and gives
// the same fold result before and after NFM-4088's data-side fix.
// ---------------------------------------------------------------------------

describe("groupKey", () => {
  it("returns identical keys for rows that share (name, value, source.title)", () => {
    const a = makeProperty({ id: "p1" })
    const b = makeProperty({ id: "p2" })
    expect(groupKey(a)).toBe(groupKey(b))
  })

  it("differs when source.title changes even if name + value match", () => {
    const a = makeProperty({
      name: "Activation Energy",
      value: "0.3",
      source: makeSource({ title: "Hall 1989" }),
    })
    const b = makeProperty({
      name: "Activation Energy",
      value: "0.3",
      source: makeSource({ title: "Owen 2023" }),
    })
    expect(groupKey(a)).not.toBe(groupKey(b))
  })

  it("returns identical keys when source.id differs but source.title matches (pre-NFM-4088 shape)", () => {
    // This pins the production-realistic case QA Tester flagged in
    // [QA-FAILED] on NFM-4087: data_sources had 4 different UUIDs all
    // pointing at the same paper, so source.id-based grouping produced
    // 4 buckets and the user saw 4 identical rows. The fix keys on
    // source.title so two measurements with the same paper title fold
    // together regardless of how many distinct data_sources UUIDs
    // happen to back them.
    const a = makeProperty({
      id: "src-aaa",
      name: "Activation Energy",
      value: "0.3",
      source: makeSource({
        id: "00000000-0000-0000-0000-000000000001",
        title: "Hall 1989 — UO2 activation energy",
      }),
    })
    const b = makeProperty({
      id: "src-bbb",
      name: "Activation Energy",
      value: "0.3",
      source: makeSource({
        id: "00000000-0000-0000-0000-000000000002",
        title: "Hall 1989 — UO2 activation energy",
      }),
    })
    expect(groupKey(a)).toBe(groupKey(b))
  })

  it("groups unsourced rows together (sentinel)", () => {
    const a = makeProperty({ source: null })
    const b = makeProperty({ source: null, id: "p2" })
    expect(groupKey(a)).toBe(groupKey(b))
  })
})

describe("groupRowsByKey", () => {
  it("folds 4 rows of UO2 activation_energy into 1 group with count=4 (post-NFM-4088 shape)", () => {
    // After NFM-4088 migration 070 collapses the data_sources UUID-title
    // duplicates, all 4 measurements reference the same canonical source.id
    // and source.title. This test pins that — a sanity check that the
    // normal "same row → fold" path still works.
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

  it("folds 4 rows whose source.id differs but source.title matches (pre-NFM-4088 shape)", () => {
    // Production-realistic case QA Tester flagged as [QA-FAILED]. Pre-NFM-4088
    // the data_sources table held 4 different UUIDs all sharing the title
    // `9320cb50-…`. Each measurement referenced a distinct UUID but the
    // same paper. Keying on `source.title` collapses those 4 rows into
    // one logical measurement, matching the user's mental model.
    const sharedTitle = "9320cb50-eb65-4178-8d2e-c56aeb848b21"
    const rows: ReadonlyArray<MaterialProperty> = [
      makeProperty({
        id: "ae-1",
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source: makeSource({
          id: "85cc8a6a-b429-487b-8d78-371740d21629",
          title: sharedTitle,
        }),
      }),
      makeProperty({
        id: "ae-2",
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source: makeSource({
          id: "d6c7c8f7-5d4a-40be-b93a-88b83015239f",
          title: sharedTitle,
        }),
      }),
      makeProperty({
        id: "ae-3",
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source: makeSource({
          id: "12e3a95c-9b83-447b-be87-cf32e39aefea",
          title: sharedTitle,
        }),
      }),
      makeProperty({
        id: "ae-4",
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source: makeSource({
          id: "b4788e26-5fd4-4f20-b924-49aede7176b6",
          title: sharedTitle,
        }),
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

  it("does NOT group rows that differ on source.title even if the values match", () => {
    // Two distinct papers measuring the same property/value at the same
    // name are not the same measurement — they must remain separate
    // rows so curators can disambiguate. Title-based grouping only folds
    // rows whose paper title is identical.
    const rows: ReadonlyArray<MaterialProperty> = [
      makeProperty({
        id: "p1",
        name: "Density",
        value: "10.5",
        source: makeSource({
          id: "00000000-0000-0000-0000-000000000001",
          title: "Hall 1989",
        }),
      }),
      makeProperty({
        id: "p2",
        name: "Density",
        value: "10.5",
        source: makeSource({
          id: "00000000-0000-0000-0000-000000000002",
          title: "Owen 2023",
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
    const source = makeSource({
      id: "00000000-0000-0000-0000-000000000003",
      title: "Same paper",
    })
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

  it("folds rows whose source.title matches even when their source.id differs (pre-NFM-4088 shape)", () => {
    // Production-reality case: 4 measurements reference 4 different
    // source.ids that all back the same paper title. With title-keyed
    // grouping these fold into a single row carrying the ×4 badge.
    const sharedTitle = "9320cb50 — UO2 activation energy"
    const rows: ReadonlyArray<MaterialProperty> = [
      makeProperty({
        id: "src-1",
        name: "Activation Energy",
        value: "0.3",
        source: makeSource({ id: "src-aaa", title: sharedTitle }),
      }),
      makeProperty({
        id: "src-2",
        name: "Activation Energy",
        value: "0.3",
        source: makeSource({ id: "src-bbb", title: sharedTitle }),
      }),
      makeProperty({
        id: "src-3",
        name: "Activation Energy",
        value: "0.3",
        source: makeSource({ id: "src-ccc", title: sharedTitle }),
      }),
      makeProperty({
        id: "src-4",
        name: "Activation Energy",
        value: "0.3",
        source: makeSource({ id: "src-ddd", title: sharedTitle }),
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

    // All four fold into one row. The ×N badge reflects the fold count.
    expect(screen.getAllByText("Activation Energy")).toHaveLength(1)
    expect(screen.getByText("×4")).toBeInTheDocument()
  })

  it("does NOT fold rows whose source.title differs even when the value matches", () => {
    // Two distinct papers at the same value must NOT fold together.
    const rows: ReadonlyArray<MaterialProperty> = [
      makeProperty({
        id: "src1",
        name: "Density",
        value: "10.5",
        source: makeSource({ id: "src-aaa", title: "Hall 1989" }),
      }),
      makeProperty({
        id: "src2",
        name: "Density",
        value: "10.5",
        source: makeSource({ id: "src-bbb", title: "Owen 2023" }),
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

  it("wraps the expanded conditions sub-table in a horizontally-scrollable container so T/P/env/dpa/notes columns stay reachable on mobile (NFM-4118)", () => {
    // NFM-4118 (QA-FOLLOWUP W2 from NFM-4087) — on a ~390px viewport,
    // the parent MaterialPropertyTable's `scroll={{x: 800}}` does NOT
    // cover the expander's hand-written sub-table (it renders nested
    // inside Ant Table and so does not inherit the parent scroll). The
    // sub-table must therefore carry its own overflow handling, with a
    // minimum width wide enough to keep all six column headers
    // readable when the user scrolls horizontally.
    const source = makeSource({ title: "NFM-4118 source" })
    const rows: ReadonlyArray<MaterialProperty> = [
      makeProperty({
        id: "n1",
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source,
        conditions: [
          makeCondition({
            id: "c-1",
            temperature: 873.15,
            pressure: 0.1,
            environment: "inert",
            irradiation_dose: 5.0,
            notes: "pre-anneal",
          }),
        ],
      }),
      makeProperty({
        id: "n2",
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source,
        conditions: [
          makeCondition({
            id: "c-2",
            temperature: 1073.15,
            pressure: 10.0,
            environment: "oxidising",
            irradiation_dose: 50.0,
            notes: "post-anneal ramp 5 K/min",
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

    const expandButton = screen.getByRole("button", { name: "展开 conditions" })
    fireEvent.click(expandButton)

    // Heading confirms the expander opened.
    const heading = screen.getByText(/底层 \d+ 条 measurement 的 conditions/)
    expect(heading).toBeInTheDocument()

    // All six column headers must be present in the expander sub-table
    // — they are reachable via horizontal scroll on narrow viewports.
    // (Headers are unique to the expander because the parent table uses
    // "属性名称" / "数值" / etc., not "温度" / "压力" / "环境".)
    expect(screen.getByText("温度")).toBeInTheDocument()
    expect(screen.getByText("压力")).toBeInTheDocument()
    expect(screen.getByText("环境")).toBeInTheDocument()
    expect(screen.getByText("辐照剂量")).toBeInTheDocument()
    expect(screen.getByText("备注")).toBeInTheDocument()

    // Scope to the expander container (the bg-slate-900/40 div holding
    // both the heading and the hand-written sub-table).
    const expanderContainer = heading.parentElement
    expect(expanderContainer).not.toBeNull()
    const subTable = expanderContainer!.querySelector("table")
    expect(subTable).not.toBeNull()

    // The sub-table must be wrapped in a horizontally-scrollable
    // container so the wider cells do not push the page off-screen on
    // narrow viewports. The wrapper may be the expander container
    // itself or a child wrapper — accept either, as long as the chain
    // from the table up to the closest scroll-bearing ancestor includes
    // an `overflow-x: auto` element.
    const wrapper = subTable!.parentElement
    expect(wrapper).not.toBeNull()
    const wrapperStyle = wrapper!.className + " " + (wrapper!.getAttribute("style") ?? "")
    expect(wrapperStyle).toMatch(/overflow-x-auto|overflow-x:\s*auto/)

    // The inner table must enforce a minimum width wide enough for the
    // six columns to remain readable when the wrapper scrolls. The
    // implementation may use either an inline `style="min-width: Npx"`
    // or a Tailwind arbitrary class `min-w-[Npx]` — both forms are
    // accepted here. The numeric value must be at least 480px (enough
    // for the six column headers plus their content to remain
    // readable when scrolled horizontally on a 390px viewport).
    const tableClass = subTable!.className
    const tableStyle = subTable!.getAttribute("style") ?? ""
    const inlineMatch = tableStyle.match(/min-width:\s*(\d+)px/)
    const tailwindMatch = tableClass.match(/min-w-\[(\d+)px\]/)
    const matchedPxRaw = inlineMatch?.[1] ?? tailwindMatch?.[1]
    expect(matchedPxRaw).not.toBeUndefined()
    const minWidthPx = matchedPxRaw !== undefined ? parseInt(matchedPxRaw, 10) : 0
    expect(minWidthPx).toBeGreaterThanOrEqual(480)
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

// ── NFM-4117 W1: ×N badge must survive narrow viewports ───────────────
//
// On viewport widths ≤ ~500px the original "计数" column (position 6 of 6,
// width 80px, total table width ~900px) lives past the right edge of
// `.ant-table-body`'s horizontal scroll. With 17-fold rows now common on
// canonical-seed materials, users on phones lose the only signal that a
// row was folded. The fix is to surface the ×N badge inside the value
// cell (column 2 of the new order) so it lives in the always-visible
// region regardless of viewport width.

describe("MaterialPropertyTable — NFM-4117 W1 narrow-viewport ×N badge", () => {
  it("renders the ×N count badge INSIDE the value cell (column 2) so it survives narrow viewports", () => {
    const source = makeSource({ title: "Narrow viewport source" })
    const rows: ReadonlyArray<MaterialProperty> = [1, 2, 3].map((i) =>
      makeProperty({
        id: `narrow-${i}`,
        name: "Activation Energy",
        value: "0.3",
        unit: "eV",
        source,
        confidence: 0.9,
      }),
    )

    const { container } = render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={rows}
        total={rows.length}
        error={null}
      />,
    )

    // The value cell (the one carrying "0.3") must contain the ×N badge
    // as a descendant — meaning the badge lives in the always-visible
    // second column, NOT in the cut-off 计数 column.
    const valueCells = container.querySelectorAll(".ant-table-cell")
    const cellWithValue = Array.from(valueCells).find((cell) =>
      cell.textContent?.includes("0.3"),
    )
    expect(cellWithValue).toBeDefined()
    expect(cellWithValue?.textContent).toMatch(/×3/)
  })

  it("does NOT render the off-screen 计数 column header", () => {
    // The 计数 column is the column that gets clipped on narrow viewports;
    // the fix moves the ×N indicator inline so that header is no longer
    // needed.
    const source = makeSource({ title: "Header test" })
    const rows: ReadonlyArray<MaterialProperty> = [1, 2].map((i) =>
      makeProperty({
        id: `ht-${i}`,
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

    expect(screen.queryByText("计数")).not.toBeInTheDocument()
  })

  it("omits the inline ×N badge when count is 1 (single-measurement rows stay clean)", () => {
    // Single-measurement rows should never render a ×N badge — the
    // inline placement must respect the same count <= 1 guard that the
    // 计数 column used.
    const rows: ReadonlyArray<MaterialProperty> = [
      makeProperty({ id: "p1", name: "Density", value: "10.5" }),
    ]

    render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={rows}
        total={rows.length}
        error={null}
      />,
    )

    // No fold → no ×N indicator anywhere in the row.
    expect(screen.queryByText(/^×/)).not.toBeInTheDocument()
  })
})

// ── NFM-4204 — row-level attribution DOM contract ─────────────────────
//
// The data-loss-notice e2e backstop (apps/web/e2e/data-loss-notice.spec.ts)
// asserts on `tr[data-attribution-status="lost"]` and
// `tr.material-property-row--data-loss`. Until NFM-4204 those selectors were
// emitted by NO component — the spec could never pass in any environment.
// These tests pin the contract at the unit level so a future refactor of the
// antd Table wiring cannot silently drop the attributes again.

describe("MaterialPropertyTable — row attribution DOM contract (NFM-4204)", () => {
  it("rowAttributionStatus returns lost when any underlying measurement is lost", () => {
    // Both rows are unsourced so they fold into ONE group (the realistic
    // mixed case: a lost measurement folds with an unadjudicated
    // duplicate — sourced rows can never share a group with a lost one
    // because migration 070 NULLs the lost row's source).
    const groups = groupRowsByKey([
      makeProperty({ id: "p1", name: "密度", value: "10.5", source: null }),
      makeProperty({
        id: "p0",
        name: "密度",
        value: "10.5",
        source: null,
        attribution: { status: "lost", lostAt: "2026-08-01", siblingPlaceholderCount: 3 },
      }),
    ])
    const row = groups[0]
    if (row === undefined) throw new Error("expected exactly one group")
    expect(rowAttributionStatus(row)).toBe("lost")
  })

  it("rowAttributionStatus returns intact when attribution is present and none lost", () => {
    const groups = groupRowsByKey([
      makeProperty({
        id: "p1",
        name: "密度",
        value: "10.5",
        attribution: { status: "intact" },
      }),
    ])
    const row = groups[0]
    if (row === undefined) throw new Error("expected exactly one group")
    expect(rowAttributionStatus(row)).toBe("intact")
  })

  it("rowAttributionStatus returns null when no measurement carries attribution", () => {
    const groups = groupRowsByKey([
      makeProperty({ id: "p1", name: "密度", value: "10.5" }),
    ])
    const row = groups[0]
    if (row === undefined) throw new Error("expected exactly one group")
    expect(rowAttributionStatus(row)).toBeNull()
  })

  it("renders data-attribution-status=lost and the --data-loss row class on lost rows", () => {
    const { container } = render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={[
          makeProperty({ id: "p-lost", name: "密度", value: "10.5", source: null, attribution: { status: "lost" } }),
          makeProperty({ id: "p-intact", name: "熔点", value: "1850", attribution: { status: "intact" } }),
          makeProperty({ id: "p-plain", name: "热导率", value: "3.0" }),
        ]}
        total={3}
        error={null}
      />,
    )

    const lostRow = container.querySelector('tr[data-attribution-status="lost"]')
    expect(lostRow).not.toBeNull()
    expect(lostRow?.className).toContain("material-property-row--data-loss")

    // Cohort scope: exactly one lost row, and only that row carries the
    // --data-loss marker.
    expect(container.querySelectorAll('tr[data-attribution-status="lost"]')).toHaveLength(1)
    expect(container.querySelectorAll("tr.material-property-row--data-loss")).toHaveLength(1)
  })

  it("renders data-attribution-status=intact on adjudicated rows without the --data-loss class", () => {
    const { container } = render(
      <MaterialPropertyTable
        {...TABLE_PROPS}
        data={[
          makeProperty({ id: "p-intact", name: "熔点", value: "1850", attribution: { status: "intact" } }),
          makeProperty({ id: "p-plain", name: "热导率", value: "3.0" }),
        ]}
        total={2}
        error={null}
      />,
    )

    const intactRow = container.querySelector('tr[data-attribution-status="intact"]')
    expect(intactRow).not.toBeNull()
    expect(intactRow?.className).not.toContain("material-property-row--data-loss")

    // Rows outside the adjudicated cohort carry no status attribute at all.
    expect(container.querySelectorAll("tr[data-attribution-status]")).toHaveLength(1)
  })
})
