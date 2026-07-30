/**
 * Tests for the ExtractionResultsList component (NFM-2249).
 *
 * The component renders rows from the literature detail panel's
 * `extraction_results` array, and must:
 *   - Show a provenance Tag with distinct color + Chinese text per
 *     `source_type` value (manual / kg_node / kg_edge).
 *   - Render `kg_edge` items as readable triples
 *     (`<sourceLabel> --<relationType>--> <targetLabel>`),
 *     resolving node labels from sibling kg_node items in the same array.
 *   - Degrade gracefully when `source_type` is absent (the live prod
 *     shape before PR #552 / NFM-2224 merges).
 *
 * Mirrors the backend Pydantic model `ExtractionResultItem` defined in
 * `apps/api/src/nfm_db/schemas/literature.py`.
 */

import { describe, expect, it } from "vitest"
import { render, screen, within } from "@testing-library/react"

import {
  ExtractionResultsList,
  buildKgNodeLabelIndex,
} from "@/app/literature/ExtractionResultsList"
import type {
  ExtractionResultItem,
  KgEdgeExtractionResultItem,
  KgNodeExtractionResultItem,
  ManualExtractionResultItem,
} from "@/lib/api-client"

const manualRow: ManualExtractionResultItem = {
  source_type: "manual",
  id: "manual-1",
  property_name: "melting_point",
  item_type: "property",
  item_data: {},
  value: 3120,
  confidence: null,
  created_at: "2026-07-31T00:00:00Z",
  source_paragraph: null,
  review_status: "approved",
}

const kgNodeRow: KgNodeExtractionResultItem = {
  source_type: "kg_node",
  id: "kg-node-uo2",
  property_name: "UO2",
  item_type: "material",
  item_data: {},
  value: null,
  confidence: 0.95,
  created_at: "2026-07-31T00:00:00Z",
  source_paragraph: "UO2 fuel is the primary nuclear fuel.",
  unit: null,
  source_page: 4,
}

const kgNodeRowDensity: KgNodeExtractionResultItem = {
  source_type: "kg_node",
  id: "kg-node-density",
  property_name: "density",
  item_type: "property",
  item_data: {},
  value: 10.97,
  confidence: 0.9,
  created_at: "2026-07-31T00:00:00Z",
  source_paragraph: null,
  unit: "g/cm^3",
  source_page: 4,
}

const kgEdgeRow: KgEdgeExtractionResultItem = {
  source_type: "kg_edge",
  id: "kg-edge-1",
  property_name: "hasProperty",
  item_type: "edge",
  item_data: {},
  value: null,
  confidence: 0.88,
  created_at: "2026-07-31T00:00:00Z",
  source_paragraph: null,
  source_node_id: "kg-node-uo2",
  source_target_id: "kg-node-density",
}

describe("ExtractionResultsList — provenance Tag (AC-1)", () => {
  it("renders a '手动录入' tag for source_type=manual", () => {
    render(<ExtractionResultsList items={[manualRow]} />)
    expect(screen.getByText("手动录入")).toBeInTheDocument()
  })

  it("renders a 'KG 实体' tag for source_type=kg_node", () => {
    render(<ExtractionResultsList items={[kgNodeRow]} />)
    expect(screen.getByText("KG 实体")).toBeInTheDocument()
  })

  it("renders a 'KG 关系' tag for source_type=kg_edge", () => {
    render(
      <ExtractionResultsList items={[kgNodeRow, kgNodeRowDensity, kgEdgeRow]} />,
    )
    expect(screen.getByText("KG 关系")).toBeInTheDocument()
  })
})

describe("ExtractionResultsList — KG edge triple (AC-2)", () => {
  it("renders a kg_edge as 'sourceLabel --relationType--> targetLabel'", () => {
    render(
      <ExtractionResultsList items={[kgNodeRow, kgNodeRowDensity, kgEdgeRow]} />,
    )

    // The edge row container is the one carrying the edge's `id` as key.
    const edgeRow = screen.getByTestId(`extraction-row-${kgEdgeRow.id}`)
    expect(within(edgeRow).getByText("UO2")).toBeInTheDocument()
    expect(within(edgeRow).getByText("hasProperty")).toBeInTheDocument()
    expect(within(edgeRow).getByText("density")).toBeInTheDocument()

    // Triple must be visually structured — source, relation, target —
    // not the legacy three-token noise.
    expect(edgeRow.textContent).toContain("UO2")
    expect(edgeRow.textContent).toContain("hasProperty")
    expect(edgeRow.textContent).toContain("density")
    expect(edgeRow.textContent).toContain("→")
  })

  it("falls back to a truncated UUID when a kg_node label cannot be resolved", () => {
    // Edge points at node ids that aren't in the kg_node list. Because
    // the backend caps nodes at 200 and edges at 400 (literature.py
    // _MAX_KG_NODES_PER_SOURCE / _MAX_KG_EDGES_PER_SOURCE), orphan
    // endpoints are routine — they must NOT render as raw 36-char
    // UUIDs that wrap and destroy row rhythm.
    const orphanEdge: KgEdgeExtractionResultItem = {
      ...kgEdgeRow,
      id: "kg-edge-orphan",
      source_node_id: "673258f9-21dc-4485-a6dd-1eb1df13ed23",
      source_target_id: "8a72bbe3-7c14-4f2a-b5d9-1c8e5a9c1f44",
    }

    render(<ExtractionResultsList items={[orphanEdge]} />)

    const edgeRow = screen.getByTestId(`extraction-row-${orphanEdge.id}`)
    const renderedText = edgeRow.textContent ?? ""

    // Full UUID must NOT appear — that was the pre-fix defect.
    expect(renderedText).not.toContain("673258f9-21dc-4485-a6dd-1eb1df13ed23")
    expect(renderedText).not.toContain("8a72bbe3-7c14-4f2a-b5d9-1c8e5a9c1f44")

    // Truncated prefix must be visible (referenceable in bug reports).
    expect(renderedText).toContain("673258f9")
    expect(renderedText).toContain("8a72bbe3")
  })

  it("does not truncate ids that are already short", () => {
    // Short identifiers (e.g. test fixtures) must pass through verbatim —
    // adding ellipsis to a 12-char id would be visual noise.
    const shortSourceEdge: KgEdgeExtractionResultItem = {
      ...kgEdgeRow,
      id: "kg-edge-short",
      source_node_id: "short-source",
      source_target_id: "short-target",
    }

    render(<ExtractionResultsList items={[shortSourceEdge]} />)

    const edgeRow = screen.getByTestId(`extraction-row-${shortSourceEdge.id}`)
    expect(edgeRow.textContent).toContain("short-source")
    expect(edgeRow.textContent).toContain("short-target")
    // No ellipsis on short ids.
    expect(edgeRow.textContent).not.toContain("…")
  })

  it("renders edge row container with flex + min-w-0 so the row height stays bounded", () => {
    // The UXDesigner review (NFM-2249) found the KgEdgeRow container
    // was a plain `<div className="text-sm">` with no `truncate`,
    // `min-w-0`, or `overflow-hidden`. A long source/target would wrap
    // and inflate the row. CSS truncation must work — className must
    // include the truncation primitives.
    render(<ExtractionResultsList items={[kgNodeRow, kgEdgeRow]} />)

    const edgeRow = screen.getByTestId(`extraction-row-${kgEdgeRow.id}`)
    expect(edgeRow.querySelector(".truncate")).not.toBeNull()
    expect(edgeRow.querySelector(".min-w-0")).not.toBeNull()
  })
})

/**
 * Regression guards for the defects found in the second Visual QA pass
 * (NFM-2249, VQA-1 .. VQA-5). Each of these was invisible to the unit
 * suite and only surfaced in rendered pixels, so they are asserted on
 * the layout primitives that produced them.
 */
describe("ExtractionResultsList — visual regression guards", () => {
  it("VQA-1: endpoint spans do not use flex-1, which sizes them equally regardless of content", () => {
    // `flex-1` is `flex: 1 1 0%` — basis 0 means both endpoint boxes get
    // the same width no matter how long their text is. Measured at 390px
    // that clipped a 20-char target while leaving 75px unused beside a
    // 7-char source. Endpoints must be sized by their content.
    render(
      <ExtractionResultsList items={[kgNodeRow, kgNodeRowDensity, kgEdgeRow]} />,
    )

    const edgeRow = screen.getByTestId(`extraction-row-${kgEdgeRow.id}`)
    const endpoints = edgeRow.querySelectorAll('[data-testid^="edge-"]')

    expect(endpoints.length).toBeGreaterThan(0)
    for (const endpoint of endpoints) {
      expect(endpoint.className).not.toContain("flex-1")
    }
  })

  it("VQA-2: nothing renders after the target label except an inert spacer", () => {
    // A trailing `→` span sat where a "go to detail" chevron would sit on
    // all 34 edge rows, reading as an affordance that does nothing.
    render(
      <ExtractionResultsList items={[kgNodeRow, kgNodeRowDensity, kgEdgeRow]} />,
    )

    const edgeRow = screen.getByTestId(`extraction-row-${kgEdgeRow.id}`)
    const target = within(edgeRow).getByTestId("edge-target")

    let sibling = target.nextElementSibling
    while (sibling !== null) {
      expect(sibling.textContent).toBe("")
      sibling = sibling.nextElementSibling
    }
  })

  it("VQA-3: header row wraps and its property name truncates", () => {
    // Without `flex-wrap` the header overflowed its container by up to
    // 67px at 390x844, clipping `置信度 92%` mid-glyph.
    render(<ExtractionResultsList items={[kgNodeRow]} />)

    const row = screen.getByTestId(`extraction-row-${kgNodeRow.id}`)
    const header = within(row).getByTestId("extraction-header")
    expect(header.className).toContain("flex-wrap")

    const propertyName = within(row).getByText(kgNodeRow.property_name)
    expect(propertyName.className).toContain("truncate")
    expect(propertyName.className).toContain("min-w-0")
  })

  it("VQA-4: the triple uses a single arrow idiom, not ASCII hyphens", () => {
    render(
      <ExtractionResultsList items={[kgNodeRow, kgNodeRowDensity, kgEdgeRow]} />,
    )

    const edgeRow = screen.getByTestId(`extraction-row-${kgEdgeRow.id}`)
    const text = edgeRow.textContent ?? ""

    expect(text).toContain("→")
    expect(text).not.toContain("--")
  })

  it("VQA-5: provenance is the only coloured pill — confidence is plain text", () => {
    // `manual` and the confidence Tag were both `color="default"`, so a
    // manual row showed three near-identical grey pills and the
    // provenance signal stopped reading as provenance (AC-1).
    render(<ExtractionResultsList items={[manualRow, kgNodeRow]} />)

    const provenance = screen.getByText("手动录入")
    const provenanceTag = provenance.classList.contains("ant-tag")
      ? provenance
      : provenance.closest(".ant-tag")
    expect(provenanceTag?.className).toContain("ant-tag-gold")

    const confidence = screen.getByText(/置信度/)
    expect(confidence.closest(".ant-tag")).toBeNull()
  })

  it("H4: no text-gray-400, which fails the 4.5:1 contrast floor on white", () => {
    const { container } = render(
      <ExtractionResultsList items={[kgNodeRow, kgNodeRowDensity, kgEdgeRow]} />,
    )
    expect(container.querySelector(".text-gray-400")).toBeNull()
  })
})

describe("ExtractionResultsList — graceful degradation (AC-5)", () => {
  it("does not render a provenance tag and does not crash when source_type is absent", () => {
    // Pre-#552 live prod shape: items have no `source_type`.
    const legacyItem = {
      id: "legacy-1",
      property_name: "legacy_property",
      item_type: "property",
      item_data: {},
      value: 42,
      confidence: 0.5,
      created_at: null,
    } as unknown as ExtractionResultItem

    expect(() => render(<ExtractionResultsList items={[legacyItem]} />)).not.toThrow()

    const row = screen.getByTestId(`extraction-row-legacy-1`)
    // No source_type → none of the provenance labels render.
    expect(within(row).queryByText("手动录入")).toBeNull()
    expect(within(row).queryByText("KG 实体")).toBeNull()
    expect(within(row).queryByText("KG 关系")).toBeNull()

    // Common fields still render.
    expect(within(row).getByText("legacy_property")).toBeInTheDocument()
  })

  it("H5: renders an explicit empty state rather than a bare bordered box", () => {
    expect(() => render(<ExtractionResultsList items={[]} />)).not.toThrow()
    expect(screen.getByTestId("extraction-empty")).toBeInTheDocument()
  })
})

describe("buildKgNodeLabelIndex", () => {
  it("indexes kg_node labels by node id so edges can resolve source/target", () => {
    const index = buildKgNodeLabelIndex([kgNodeRow, kgNodeRowDensity, kgEdgeRow])
    expect(index.get("kg-node-uo2")).toBe("UO2")
    expect(index.get("kg-node-density")).toBe("density")
    // Edges themselves are not indexed.
    expect(index.has("kg-edge-1")).toBe(false)
  })
})