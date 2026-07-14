import { describe, it, expect } from "vitest"
import { render, screen, fireEvent, within } from "@testing-library/react"

// ─── Inline type references (avoid import issues with test setup) ─

interface V4FigureResult {
  readonly page_number: number
  readonly source_file: string
  readonly extraction: {
    readonly figure_type: string
    readonly plot_data: {
      readonly title: string
      readonly plot_type: string
      readonly x_axis: {
        readonly label: string
        readonly unit: string
        readonly values: readonly number[]
        readonly scale: string
      }
      readonly y_axis: {
        readonly label: string
        readonly unit: string
        readonly values: readonly number[]
        readonly scale: string
      }
      readonly series: readonly {
        readonly name: string
        readonly values: readonly number[]
      }[]
      readonly confidence: number
    } | null
    readonly table_data: unknown
    readonly source_image_path: string | null
    readonly provider: string
    readonly model: string
    readonly extraction_time_ms: number
    readonly fallback_used: boolean
  }
}

interface V4TableResult {
  readonly page_number: number
  readonly source_file: string
  readonly table_data: {
    readonly title: string
    readonly headers: {
      readonly columns: readonly string[]
      readonly sub_headers?: readonly string[] | null
    }
    readonly rows: readonly {
      readonly value: string
      readonly row_span: number
      readonly col_span: number
      readonly is_header: boolean
      readonly confidence: number
    }[][]
    readonly num_columns: number
    readonly num_rows: number
    readonly has_merged_cells: boolean
    readonly notes: readonly string[]
    readonly confidence: number
  }
}

// ─── Factory helpers ──────────────────────────────────────────────

function createFigureResult(
  overrides: Partial<V4FigureResult> = {},
): V4FigureResult {
  return {
    page_number: 1,
    source_file: "test.pdf",
    extraction: {
      figure_type: "plot",
      plot_data: {
        title: "Thermal Conductivity vs Temperature",
        plot_type: "line",
        x_axis: {
          label: "Temperature",
          unit: "K",
          values: [300, 400, 500],
          scale: "linear",
        },
        y_axis: {
          label: "κ",
          unit: "W/m·K",
          values: [10, 15, 20],
          scale: "linear",
        },
        series: [
          {
            name: "UO2",
            values: [10, 15, 20],
            color: "blue",
            marker_style: "circle",
          },
        ],
        legend_entries: ["UO2"],
        annotations: [],
        confidence: 0.85,
      },
      table_data: null,
      source_image_path: "/figures/fig1.png",
      provider: "openai",
      model: "gpt-4o",
      extraction_time_ms: 1200,
      fallback_used: false,
    },
    ...overrides,
  } as V4FigureResult
}

function createTableResult(
  overrides: Partial<V4TableResult> = {},
): V4TableResult {
  return {
    page_number: 2,
    source_file: "test.pdf",
    table_data: {
      title: "Material Properties Summary",
      headers: {
        columns: ["Material", "Property", "Value", "Unit"],
        sub_headers: null,
      },
      rows: [
        [
          { value: "UO2", row_span: 1, col_span: 1, is_header: false, confidence: 1.0 },
          { value: "Thermal Conductivity", row_span: 1, col_span: 1, is_header: false, confidence: 1.0 },
          { value: "3.5", row_span: 1, col_span: 1, is_header: false, confidence: 0.9 },
          { value: "W/m·K", row_span: 1, col_span: 1, is_header: false, confidence: 1.0 },
        ],
      ],
      num_columns: 4,
      num_rows: 1,
      has_merged_cells: false,
      notes: ["Measured at 298K"],
      confidence: 0.92,
    },
    ...overrides,
  } as V4TableResult
}

// ─── Test wrapper ──────────────────────────────────────────────────
// Note: We intentionally do NOT wrap with ConfigProvider + custom algorithm
// here. Ant Design's cssinjs theme derivation (getDerivativeToken) calls
// an internal `derivative()` function that is not available in jsdom,
// causing "TypeError: derivative is not a function". The default Ant
// Design theme renders correctly in jsdom without a custom algorithm.

function renderWithAntd(ui: React.ReactElement) {
  return render(ui)
}

// ─── Helpers ───────────────────────────────────────────────────────

/**
 * Tab labels contain Ant Design icons as sibling elements, so text is
 * split across nodes. We match by finding the tab role and checking its
 * text content rather than using getByText on the label string.
 */
function getTabByText(pattern: RegExp): HTMLElement {
  const tabs = screen.getAllByRole("tab")
  const match = tabs.find(
    (tab) => tab.textContent && pattern.test(tab.textContent),
  )
  if (!match) {
    throw new Error(`No tab found matching ${pattern}`)
  }
  return match
}

// ─── ExtractionResult component tests ────────────────────────────────

describe("ExtractionResult", () => {
  let ExtractionResult: React.ComponentType<{
    readonly text?: string
    readonly figures: readonly V4FigureResult[]
    readonly tables: readonly V4TableResult[]
    readonly loading?: boolean
    readonly error?: Error | null
  }>

  beforeAll(async () => {
    const mod = await import(
      "@/components/v4-extraction/extraction-result",
    )
    ExtractionResult = mod.default
  })

  // --- Loading state ---

  it("shows loading spinner when loading is true", () => {
    renderWithAntd(
      <ExtractionResult figures={[createFigureResult()]} tables={[]} loading />,
    )
    // Ant Design Spin renders as a div with class containing "ant-spin"
    const spinner = document.querySelector(".ant-spin")
    expect(spinner).not.toBeNull()
  })

  // --- Error state ---

  it("shows error message when error is provided", () => {
    const error = new Error("Network timeout")
    renderWithAntd(
      <ExtractionResult figures={[createFigureResult()]} tables={[]} error={error} />,
    )
    expect(screen.getByText(/Network timeout/)).toBeDefined()
  })

  // --- Empty state ---

  it("shows empty state when no multimodal data is available", () => {
    renderWithAntd(<ExtractionResult figures={[]} tables={[]} />)
    expect(
      screen.getByText(/No multimodal extraction results/),
    ).toBeDefined()
  })

  // --- Tabs rendering ---

  it("renders tab labels for text, figures, and tables", () => {
    const text = "Extracted text content from the document."
    renderWithAntd(
      <ExtractionResult
        text={text}
        figures={[createFigureResult()]}
        tables={[createTableResult()]}
      />,
    )
    expect(getTabByText(/Text/)).toBeDefined()
    expect(getTabByText(/Figures/)).toBeDefined()
    expect(getTabByText(/Tables/)).toBeDefined()
  })

  it("defaults to figures tab when no text is provided", () => {
    renderWithAntd(
      <ExtractionResult figures={[createFigureResult()]} tables={[createTableResult()]} />,
    )
    const figuresTab = getTabByText(/Figures/)
    expect(figuresTab).not.toHaveAttribute("aria-disabled", "true")
  })

  it("disables figures tab when no figures are present", () => {
    renderWithAntd(
      <ExtractionResult text="Some text" figures={[]} tables={[createTableResult()]} />,
    )
    const figuresTab = getTabByText(/Figures/)
    expect(figuresTab).toHaveAttribute("aria-disabled", "true")
  })

  it("disables tables tab when no tables are present", () => {
    renderWithAntd(
      <ExtractionResult figures={[createFigureResult()]} tables={[]} />,
    )
    const tablesTab = getTabByText(/Tables/)
    expect(tablesTab).toHaveAttribute("aria-disabled", "true")
  })

  // --- Figure count badge ---

  it("shows figure count badge when figures exist", () => {
    renderWithAntd(
      <ExtractionResult
        figures={[createFigureResult(), createFigureResult({ page_number: 3 })]}
        tables={[]}
      />,
    )
    expect(screen.getByText("(2)")).toBeDefined()
  })

  it("shows table count badge when tables exist", () => {
    renderWithAntd(
      <ExtractionResult
        figures={[]}
        tables={[createTableResult(), createTableResult({ page_number: 5 })]}
      />,
    )
    const badges = screen.getAllByText("(2)")
    expect(badges.length).toBeGreaterThanOrEqual(1)
  })

  // --- Summary statistics ---

  it("shows multimodal summary with correct counts", () => {
    renderWithAntd(
      <ExtractionResult figures={[createFigureResult()]} tables={[createTableResult()]} />,
    )
    expect(screen.getByText(/Avg Confidence/)).toBeDefined()
    // avg_confidence = (0.85 + 0.92) / 2 = 0.885 → 89% after toFixed(0)
    expect(screen.getByText("89")).toBeDefined()
  })

  it("does not show summary card when no multimodal data", () => {
    renderWithAntd(<ExtractionResult figures={[]} tables={[]} />)
    expect(screen.queryByText(/Avg Confidence/)).toBeNull()
  })
})

// ─── FigureViewer tests ───────────────────────────────────────────

describe("FigureViewer", () => {
  let FigureViewer: React.ComponentType<{
    readonly figures: readonly V4FigureResult[]
  }>

  beforeAll(async () => {
    const mod = await import(
      "@/components/v4-extraction/figure-viewer",
    )
    FigureViewer = mod.default
  })

  it("shows empty state when no figures are provided", () => {
    renderWithAntd(<FigureViewer figures={[]} />)
    expect(
      screen.getByText(/No extracted figures/),
    ).toBeDefined()
  })

  it("renders figure titles in thumbnail grid", () => {
    const figures = [
      createFigureResult({
        page_number: 0,
        extraction: {
          ...createFigureResult().extraction,
          plot_data: {
            ...createFigureResult().extraction.plot_data!,
            title: "Phase Diagram",
          },
        },
      }),
      createFigureResult({ page_number: 1 }),
    ]
    renderWithAntd(<FigureViewer figures={figures} />)
    expect(screen.getByText("Phase Diagram")).toBeDefined()
    // Second figure: page_number=1 → badge shows "p.2"
    expect(screen.getByText("p.2")).toBeDefined()
  })

  it("renders figure type labels", () => {
    renderWithAntd(<FigureViewer figures={[createFigureResult()]} />)
    expect(screen.getByText("plot")).toBeDefined()
  })

  it("thumbnail cards are keyboard accessible", () => {
    renderWithAntd(<FigureViewer figures={[createFigureResult()]} />)
    const card = screen.getByRole("button", { name: /View figure/ })
    expect(card).toBeDefined()
    expect(card).toHaveAttribute("tabindex", "0")
  })

  it("opens lightbox when thumbnail is clicked", () => {
    renderWithAntd(<FigureViewer figures={[createFigureResult()]} />)
    const card = screen.getByRole("button", { name: /View figure/ })
    fireEvent.click(card)
    // After click, lightbox renders as a role="dialog" overlay
    const dialog = screen.getByRole("dialog")
    expect(dialog).toBeDefined()
    // Lightbox should show the figure title (scoped to dialog to avoid thumbnail match)
    within(dialog).getByText("Thermal Conductivity vs Temperature")
  })
})

// ─── TableViewer tests ───────────────────────────────────────────────

describe("TableViewer", () => {
  let TableViewer: React.ComponentType<{
    readonly tables: readonly V4TableResult[]
  }>

  beforeAll(async () => {
    const mod = await import(
      "@/components/v4-extraction/table-viewer",
    )
    TableViewer = mod.default
  })

  it("shows empty state when no tables are provided", () => {
    renderWithAntd(<TableViewer tables={[]} />)
    expect(
      screen.getByText(/No extracted tables/),
    ).toBeDefined()
  })

  it("renders table title when present", () => {
    renderWithAntd(<TableViewer tables={[createTableResult()]} />)
    expect(
      screen.getByText("Material Properties Summary"),
    ).toBeDefined()
  })

  it("renders table headers correctly", () => {
    renderWithAntd(<TableViewer tables={[createTableResult()]} />)
    expect(screen.getByText("Material")).toBeDefined()
    expect(screen.getByText("Property")).toBeDefined()
    expect(screen.getByText("Value")).toBeDefined()
    expect(screen.getByText("Unit")).toBeDefined()
  })

  it("renders table cell values", () => {
    renderWithAntd(<TableViewer tables={[createTableResult()]} />)
    expect(screen.getByText("UO2")).toBeDefined()
    expect(screen.getByText("3.5")).toBeDefined()
    expect(screen.getByText("W/m·K")).toBeDefined()
  })

  it("renders table metadata (columns × rows)", () => {
    renderWithAntd(<TableViewer tables={[createTableResult()]} />)
    const meta = screen.getByText(/4 columns/)
    expect(meta).toBeDefined()
    expect(meta.textContent).toContain("1 rows")
  })

  it("shows merged cells indicator when applicable", () => {
    renderWithAntd(
      <TableViewer
        tables={[
          createTableResult({
            table_data: {
              ...createTableResult().table_data,
              has_merged_cells: true,
            },
          }),
        ]}
      />,
    )
    expect(
      screen.getByText(/merged cells detected/),
    ).toBeDefined()
  })

  it("renders footnotes when present", () => {
    renderWithAntd(<TableViewer tables={[createTableResult()]} />)
    expect(screen.getByText(/Measured at 298K/)).toBeDefined()
  })

  it("renders page source and page number info", () => {
    renderWithAntd(<TableViewer tables={[createTableResult()]} />)
    // page_number=2 → component renders "Page 3"
    expect(screen.getByText("Page 3")).toBeDefined()
    expect(screen.getByText("test.pdf")).toBeDefined()
  })
})

// ─── TextSection tests ──────────────────────────────────────────────

describe("TextSection", () => {
  let TextSection: React.ComponentType<{
    readonly text: string
    readonly maxHeight?: number
  }>

  beforeAll(async () => {
    const mod = await import(
      "@/components/v4-extraction/text-section",
    )
    TextSection = mod.default
  })

  it("shows empty state when text is empty", () => {
    renderWithAntd(<TextSection text="" />)
    expect(screen.getByText(/No extracted text/)).toBeDefined()
  })

  it("renders text content", () => {
    const text = "Uranium dioxide exhibits high thermal conductivity."
    renderWithAntd(<TextSection text={text} />)
    expect(screen.getByText(text)).toBeDefined()
  })

  it("truncates long text with expand button", () => {
    const longText = "A".repeat(1500)
    renderWithAntd(<TextSection text={longText} />)
    const expandBtn = screen.getByRole("button", { name: /Expand All/ })
    expect(expandBtn).toBeDefined()
  })

  it("does not show expand button for short text", () => {
    renderWithAntd(<TextSection text="Short text." />)
    expect(
      screen.queryByRole("button", { name: /Expand All/ }),
    ).toBeNull()
  })

  it("expands text when button is clicked", () => {
    const longText = "A".repeat(1500)
    renderWithAntd(<TextSection text={longText} />)
    const expandBtn = screen.getByRole("button", { name: /Expand All/ })
    fireEvent.click(expandBtn)
    expect(
      screen.getByRole("button", { name: /Collapse/ }),
    ).toBeDefined()
  })
})
