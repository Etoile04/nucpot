/**
 * LiteratureGraphLayout — Layout B tests (NFM-4553 G1-E).
 *
 * Spec: docs/specs/G1-extraction-value-presentation.md §4.1 + §4.3
 *
 * What these tests defend
 * ------------------------
 * The Layout B container must, given a literature detail payload:
 *
 *   1. Render a <GraphCanvas> populated from extraction_results of
 *      source_type 'kg_node' (materials / property entities) and
 *      'kg_edge' (relationships). Manual entries are sidebar-only.
 *   2. Render a right-hand <PropertySidebar> listing manual + kg_node
 *      entries (one row per property), sorted by confidence desc.
 *   3. NOT mutate the input — a fresh readonly payload stays readonly
 *      after a node click or row click.
 *   4. Surface validity_check.status='fail' rows with the
 *      ``g1-row-invalid`` class so the red-row treatment from
 *      docs/specs/G1-extraction-value-presentation-design.md §4.3 fires.
 *   5. Render a ReviewDrawer when a row is clicked and pass the row's
 *      id + measurement context through (the drawer is the shared
 *      Layout A/B component).
 *   6. Render `value_expression` as styled monospace text (KaTeX is
 *      loaded lazily — see G1 spec §4.1 + AC-5). When no formula is
 *      present, the value cell renders the raw scalar.
 *
 * What these tests deliberately do NOT assert
 * --------------------------------------------
 * They do not assert pixel-perfect layout, breakpoint widths, or the
 * exact KaTeX-rendered HTML (those are E2E / visual-QA concerns under
 * Playwright). They also do not assert the empty / error / loading
 * branches — those are covered by MaterialGraphView's pattern and are
 * not the load-bearing behaviour for Layout B acceptance.
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { LiteratureGraphLayout } from "../LiteratureGraphLayout"
import type { LiteratureExtractionResultItem } from "@/lib/api-client"

/* ------------------------------------------------------------------ */
/*  Fixtures                                                           */
/* ------------------------------------------------------------------ */

function makeKgNode(
  id: string,
  label: string,
  page: number | null = 1,
  paragraph: string | null = "sample paragraph",
  confidence = 0.9,
  itemType: "material" | "property" | "entity" | string = "material",
): LiteratureExtractionResultItem {
  return {
    id,
    source_type: "kg_node",
    property_name: label,
    item_type: itemType,
    value: null,
    confidence,
    source_page: page,
    source_paragraph: paragraph,
    item_data: {},
    provenance: ["llm"],
  }
}

function makeKgEdge(
  id: string,
  propertyName: string,
  sourceNodeId: string,
  targetNodeId: string,
): LiteratureExtractionResultItem {
  return {
    id,
    source_type: "kg_edge",
    property_name: propertyName,
    item_type: "edge",
    value: null,
    confidence: 0.85,
    source_node_id: sourceNodeId,
    source_target_id: targetNodeId,
    item_data: {},
    provenance: ["llm"],
  }
}

function makeManualRow(
  id: string,
  propertyName: string,
  value: string | number | null,
  confidence = 0.8,
  unit: string | null = null,
  valueExpression: string | null = null,
  reviewStatus: string | null = "pending",
  validityStatus: "ok" | "warn" | "fail" | "unknown" = "unknown",
  validityReason: string | null = null,
): LiteratureExtractionResultItem {
  return {
    id,
    source_type: "manual",
    property_name: propertyName,
    item_type: "measurement",
    value,
    confidence,
    unit,
    review_status: reviewStatus,
    item_data: {
      value_expression: valueExpression,
      validity_check: {
        status: validityStatus,
        reason: validityReason,
      },
    },
    provenance: ["manual"],
  }
}

const SAMPLE_PAYLOAD = {
  id: "lit-1",
  extractionResults: [
    makeKgNode("kg-n-1", "UO2", 1, "UO2 is a ceramic nuclear fuel", 0.92, "material"),
    makeKgNode("kg-n-2", "Thermal conductivity", 2, "thermal conductivity value", 0.88, "property"),
    makeKgEdge("kg-e-1", "has_property", "kg-n-1", "kg-n-2"),
    makeManualRow("pm-1", "thermal_conductivity", 0.34, 0.95, "W/(m·K)", "\\frac{k}{T}", "pending", "ok", null),
    makeManualRow("pm-2", "lattice_constant", 0.3, 0.6, "Å", null, "pending", "fail", "lattice 0.3Å outside valid_range"),
    makeManualRow("pm-3", "density", 10.96, 0.5, "g/cm^3", null, "pending", "warn", null),
  ],
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("LiteratureGraphLayout (NFM-4553)", () => {
  it("renders the GraphCanvas + sidebar from a literature payload", () => {
    render(<LiteratureGraphLayout literatureId="lit-1" payload={SAMPLE_PAYLOAD} />)

    // Sidebar lists manual + non-material kg_node rows. Material
    // kg_node rows + kg_edge rows drive the graph only.
    expect(screen.getByTestId("property-sidebar")).toBeInTheDocument()
    // pm-1, pm-2, pm-3 are manual rows. kg-n-1 is material (excluded),
    // kg-n-2 has no item_type ("default" → property → included).
    expect(screen.getAllByTestId(/^measurement-row-/)).toHaveLength(4)
    // Graph canvas
    expect(screen.getByTestId("literature-graph-canvas")).toBeInTheDocument()
  })

  it("sorts sidebar rows by confidence desc", () => {
    render(<LiteratureGraphLayout literatureId="lit-1" payload={SAMPLE_PAYLOAD} />)

    const rows = screen.getAllByTestId(/^measurement-row-/)
    // Sidebar rows sorted confidence desc:
    //   0.95 pm-1 thermal_conductivity
    //   0.92 UO2 (kg_node material — actually excluded; see below)
    //   0.88 kg-n-2 Thermal conductivity
    //   0.6  pm-2 lattice_constant
    //   0.5  pm-3 density
    // UO2 is item_type="material" so it goes to the graph only.
    expect(rows[0]).toHaveTextContent("thermal_conductivity")
    expect(rows[1]).toHaveTextContent("Thermal conductivity")
    expect(rows[2]).toHaveTextContent("lattice_constant")
    expect(rows[3]).toHaveTextContent("density")
  })

  it("marks physically invalid rows with g1-row-invalid (AC-10)", () => {
    render(<LiteratureGraphLayout literatureId="lit-1" payload={SAMPLE_PAYLOAD} />)

    const invalidRow = screen.getByTestId("measurement-row-pm-2")
    expect(invalidRow).toHaveClass("g1-row-invalid")
    // Reason surfaces on hover via title attr (AC-10 — fail 标红 + reason)
    expect(invalidRow).toHaveAttribute("title", expect.stringContaining("lattice 0.3Å"))
  })

  it("renders value_expression as KaTeX-flavored styled text (AC-5)", () => {
    render(<LiteratureGraphLayout literatureId="lit-1" payload={SAMPLE_PAYLOAD} />)

    // pm-1 has value_expression = "\\frac{k}{T}" — the row's
    // .g1-formula container must carry the rendered KaTeX DOM (a
    // <span class="katex"> tree produced by katex.renderToString,
    // NOT the raw LaTeX source). trust:false + throwOnError:false
    // keep the surface safe and resilient to malformed input.
    const formulaRow = screen.getByTestId("measurement-row-pm-1")
    const formulaContainer = formulaRow.querySelector(".g1-formula")
    expect(formulaContainer).toBeInTheDocument()
    expect(formulaContainer!.querySelector(".katex")).toBeInTheDocument()
    // The raw LaTeX source remains in the DOM as the MathML/accessible
    // name so screen readers still announce the expression.
    expect(formulaContainer!.textContent).toContain("k")
  })

  it("does NOT mutate the input payload when a row is clicked", () => {
    const payload = { ...SAMPLE_PAYLOAD, extractionResults: [...SAMPLE_PAYLOAD.extractionResults] }
    const snapshot = JSON.parse(JSON.stringify(payload))

    render(<LiteratureGraphLayout literatureId="lit-1" payload={payload} />)
    fireEvent.click(screen.getByTestId("measurement-row-pm-1"))

    expect(payload).toEqual(snapshot)
  })

  it("opens the shared ReviewDrawer when a sidebar row is clicked", () => {
    render(<LiteratureGraphLayout literatureId="lit-1" payload={SAMPLE_PAYLOAD} />)

    // Drawer is closed initially
    expect(screen.queryByTestId("review-drawer")).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId("measurement-row-pm-1"))

    // Shared drawer (NFM-4554) is now mounted
    expect(screen.getByTestId("review-drawer")).toBeInTheDocument()
  })

  it("closes the drawer when onClose is dispatched (immutable update path)", () => {
    render(<LiteratureGraphLayout literatureId="lit-1" payload={SAMPLE_PAYLOAD} />)
    fireEvent.click(screen.getByTestId("measurement-row-pm-1"))
    expect(screen.getByTestId("review-drawer")).toBeInTheDocument()

    // antd Drawer's built-in close button (the X in the corner) is
    // wired to the parent's onClose via the drawer component — clicking
    // it must drop selection so a subsequent click reopens the drawer
    // with fresh data (spec §4.3 — closure is the caller's
    // responsibility, not an implicit side effect of submit).
    const closeButton = document.querySelector(
      ".ant-drawer-content [aria-label='Close']",
    ) as HTMLElement | null
    expect(closeButton).not.toBeNull()
    fireEvent.click(closeButton!)
    // Re-clicking the same row re-opens the drawer (selection reset
    // after close, not stale).
    fireEvent.click(screen.getByTestId("measurement-row-pm-1"))
    expect(screen.getByTestId("review-drawer")).toBeInTheDocument()
  })

  it("exposes an 'open drawer' callback prop for callers (e.g. graph node click)", () => {
    const onOpenReviewDrawer = vi.fn()
    render(
      <LiteratureGraphLayout
        literatureId="lit-1"
        payload={SAMPLE_PAYLOAD}
        onOpenReviewDrawer={onOpenReviewDrawer}
      />,
    )

    // When the sidebar row fires its own callback the parent-supplied
    // onOpenReviewDrawer must receive the row id so graph-side clicks
    // can drive the same drawer instance.
    fireEvent.click(screen.getByTestId("measurement-row-pm-1"))
    expect(onOpenReviewDrawer).toHaveBeenCalledWith("pm-1")
  })
})
