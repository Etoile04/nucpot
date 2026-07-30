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

  it("falls back to the raw UUID when a kg_node label cannot be resolved", () => {
    // Edge points at a node id that isn't in the kg_node list.
    const orphanEdge: KgEdgeExtractionResultItem = {
      ...kgEdgeRow,
      id: "kg-edge-orphan",
      source_node_id: "kg-node-missing-source",
      source_target_id: "kg-node-missing-target",
    }

    render(<ExtractionResultsList items={[orphanEdge]} />)

    const edgeRow = screen.getByTestId(`extraction-row-${orphanEdge.id}`)
    // Resolved labels missing → show truncated ids rather than crash.
    expect(edgeRow.textContent).toContain("missing-source")
    expect(edgeRow.textContent).toContain("missing-target")
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

  it("does not crash on an empty list", () => {
    expect(() => render(<ExtractionResultsList items={[]} />)).not.toThrow()
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