/**
 * Deterministic mock fixtures for the KG Search E2E flow (NFM-1398).
 *
 * The fixtures mirror the live API contracts from
 *   apps/web/src/lib/kg-search-api.ts
 *   apps/web/src/lib/kg-node-api.ts
 * so they can be served verbatim via Playwright's `page.route()` without
 * re-shaping the payload on the client side.
 *
 * The search endpoint wraps the response in `{ success, data }`:
 *   fetchKgSearch     -> /api/v1/kg/search?q=...&status=active&limit=20&offset=0
 *   fetchKgNode       -> /api/v1/kg/nodes/{type}/{id}
 *   fetchKgRelations  -> /api/v1/kg/nodes/{id}/relations
 */

export const KG_SEARCH_QUERY = "UO2"

export const KG_SEARCH_RESPONSE = {
  success: true,
  data: {
    items: [
      {
        id: "mat-uo2-001",
        node_type: "Material",
        label: "UO2",
        aliases: ["Uranium dioxide", "urania"],
        properties: {
          formula: "UO2",
          crystal_structure: "fluorite",
          density_g_cm3: 10.97,
        },
        confidence: 0.98,
        status: "active",
        source_id: "src-001",
      },
      {
        id: "prop-bandgap-002",
        node_type: "Property",
        label: "Band gap (UO2)",
        aliases: ["Eg"],
        properties: {
          unit: "eV",
          value: 2.1,
          measurement_temperature_K: 300,
        },
        confidence: 0.87,
        status: "active",
        source_id: "src-002",
      },
    ],
    total: 2,
    limit: 20,
    offset: 0,
  },
}

export const KG_NODE_DETAIL_RESPONSE = {
  success: true,
  data: {
    id: "mat-uo2-001",
    node_type: "Material",
    label: "UO2",
    aliases: ["Uranium dioxide", "urania"],
    properties: {
      formula: "UO2",
      crystal_structure: "fluorite",
      density_g_cm3: 10.97,
    },
    confidence: 0.98,
    status: "active",
    source_id: "src-001",
  },
}

export const KG_RELATIONS_RESPONSE = {
  success: true,
  data: {
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
  },
}

/**
 * Routes used by the flow. Match against full URL because the relative
 * URL inside `fetchKgNode` is prefixed with the page origin.
 */
export const KG_ROUTES = {
  search: /\/api\/v1\/kg\/search/,
  nodeDetail: (type: string, id: string) =>
    new RegExp(`/api/v1/kg/nodes/${type}/${id}$`),
  relations: (id: string) =>
    new RegExp(`/api/v1/kg/nodes/${id}/relations`),
}

type Route = import("@playwright/test").Route

function jsonHeaders(): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
  }
}

export function fulfillJson(
  route: Route,
  body: unknown,
  status = 200,
): Promise<void> {
  return route.fulfill({
    status,
    contentType: "application/json",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  })
}