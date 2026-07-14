/**
 * Material Graph subgraph fixtures for the Phase 2 E2E suite.
 *
 * Decoupled from backend availability — fulfilled via page.route in each
 * spec. Covers the focal material node plus a small neighborhood of:
 *   properties, experiments, conditions, adjacent materials, publications.
 *
 * Node ids follow the NFM-1258.3 backend contract — focal materials
 * carry `material:<id>` and the rest map to GraphNodeType via
 * `toGraphNodeType()` in the API client.
 *
 * Spec: NFM-1401
 */

import type { KgGraphApiResponse } from "@/lib/materials-api"

export const KG_GRAPH_SUBGRAPH_001: KgGraphApiResponse = {
  nodes: [
    {
      id: "material:m_001",
      label: "氧化锆 (ZrO₂)",
      type: "Material",
    },
    {
      id: "material:m_007",
      label: "氧化钇 (Y₂O₃)",
      type: "Material",
    },
    {
      id: "property:p_001",
      label: "Density",
      type: "Property",
    },
    {
      id: "property:p_002",
      label: "Melting Point",
      type: "Property",
    },
    {
      id: "property:p_006",
      label: "Thermal Conductivity",
      type: "Property",
    },
    {
      id: "experiment:e_001",
      label: "XRD pattern at 1200°C",
      type: "Experiment",
    },
    {
      id: "condition:c_001",
      label: "1200°C / 1 atm",
      type: "Condition",
    },
    {
      id: "publication:pub_001",
      label: "Smith 2019",
      type: "Publication",
    },
  ],
  edges: [
    {
      source: "material:m_001",
      target: "property:p_001",
      type: "HAS_PROPERTY",
    },
    {
      source: "material:m_001",
      target: "property:p_002",
      type: "HAS_PROPERTY",
    },
    {
      source: "material:m_001",
      target: "property:p_006",
      type: "HAS_PROPERTY",
    },
    {
      source: "material:m_001",
      target: "experiment:e_001",
      type: "MEASURED_BY",
    },
    {
      source: "experiment:e_001",
      target: "condition:c_001",
      type: "AT_CONDITION",
    },
    {
      source: "experiment:e_001",
      target: "publication:pub_001",
      type: "CITED_IN",
    },
    {
      source: "material:m_001",
      target: "material:m_007",
      type: "DOPED_WITH",
    },
  ],
}

/** Empty subgraph — exercises the empty-state UI path. */
export const KG_GRAPH_SUBGRAPH_EMPTY: KgGraphApiResponse = {
  nodes: [],
  edges: [],
}
