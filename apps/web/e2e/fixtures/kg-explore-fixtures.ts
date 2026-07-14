/**
 * Mock data fixtures for the KG Exploration E2E test (NFM-1397).
 *
 * Three deterministic graphs:
 *   - exploreGraph: the response from GET /api/v1/kg/graph?limit=...
 *     used by the /kg/explore page on initial render.
 *   - expandedGraph: a depth-1 expansion payload used by the
 *     double-click → expand interaction.
 *   - detailNode: the response from GET /api/v1/kg/nodes/{type}/{id}
 *   - detailRelations: the response from GET /api/v1/kg/nodes/{id}/relations
 *
 * Node IDs are deliberately URL-safe (no `:` separator) because the
 * explorer click handler does
 *   `router.push(/kg/nodes/${node.type}/${node.id})`
 * which becomes part of the App Router path segment. Using colon-free
 * ids keeps the resulting URL matchable from a regex.
 *
 * The values are intentionally compact (three nodes + two edges) so the
 * force-directed layout settles quickly and the assertions don't depend
 * on layout timing.
 *
 * Spec: NFM-1397.
 */

// ── Initial /kg/explore payload ─────────────────────────────────────

export const MOCK_EXPLORE_GRAPH = {
  nodes: [
    {
      id: "mat-zr-alloy-001",
      label: "Zirconium Alloy",
      type: "Material",
      properties: { category: "structural" },
    },
    {
      id: "prop-tensile-strength",
      label: "Tensile Strength",
      type: "Property",
      properties: { unit: "MPa" },
    },
    {
      id: "exp-corrosion-test-007",
      label: "Corrosion Test 007",
      type: "Experiment",
      properties: { method: "salt-spray" },
    },
  ],
  edges: [
    {
      source: "mat-zr-alloy-001",
      target: "prop-tensile-strength",
      type: "measured_by",
    },
    {
      source: "mat-zr-alloy-001",
      target: "exp-corrosion-test-007",
      type: "tested_in",
    },
  ],
}

// ── Expanded payload for double-click expansion ─────────────────────

/**
 * Returned when a user double-clicks the Zirconium Alloy node. Adds two
 * more nodes so the test can assert that the graph grew.
 */
export const MOCK_EXPANDED_GRAPH = {
  nodes: [
    {
      id: "mat-zr-alloy-001",
      label: "Zirconium Alloy",
      type: "Material",
    },
    {
      id: "prop-yield-strength",
      label: "Yield Strength",
      type: "Property",
    },
    {
      id: "pub-zr-alloy-corrosion-2024",
      label: "Zr Alloy Corrosion 2024",
      type: "Publication",
    },
  ],
  edges: [
    {
      source: "mat-zr-alloy-001",
      target: "prop-yield-strength",
      type: "measured_by",
    },
    {
      source: "mat-zr-alloy-001",
      target: "pub-zr-alloy-corrosion-2024",
      type: "reported_in",
    },
  ],
}

// ── Detail page fixtures ────────────────────────────────────────────

/**
 * Mock for `GET /api/v1/kg/nodes/material/mat-zr-alloy-001`.
 * Matches the KGNodeDetail shape used by `kg-node-api.ts`.
 */
export const MOCK_DETAIL_NODE = {
  id: "mat-zr-alloy-001",
  node_type: "Material",
  label: "Zirconium Alloy",
  aliases: ["Zr-Alloy", "ZIRLO"],
  properties: {
    formula: "Zr-Sn-Nb",
    density_g_cm3: 6.5,
    crystal_structure: "HCP",
  },
  confidence: 0.94,
  status: "verified",
  source_id: "src-orNL-2024",
}

/** Mock for `GET /api/v1/kg/nodes/mat-zr-alloy-001/relations?limit=50&offset=0`. */
export const MOCK_DETAIL_RELATIONS = {
  items: [
    {
      id: "rel-zr-tensile-001",
      relation_type: "measured_by",
      confidence: 0.92,
      properties: { method: "uniaxial_tension" },
      source_node: {
        id: "mat-zr-alloy-001",
        node_type: "Material",
        label: "Zirconium Alloy",
        aliases: ["Zr-Alloy"],
        properties: {},
        confidence: 0.94,
        status: "verified",
        source_id: "src-orNL-2024",
      },
      target_node: {
        id: "prop-tensile-strength",
        node_type: "Property",
        label: "Tensile Strength",
        aliases: [],
        properties: { unit: "MPa" },
        confidence: 0.9,
        status: "verified",
        source_id: null,
      },
    },
  ],
  total: 1,
  limit: 50,
  offset: 0,
}

/** Convenience: IDs used by the tests for stable assertions. */
export const KG_NODE_IDS = {
  ZR_ALLOY: "mat-zr-alloy-001",
  TENSILE: "prop-tensile-strength",
  CORROSION: "exp-corrosion-test-007",
} as const