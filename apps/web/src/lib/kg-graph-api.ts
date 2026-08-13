/**
 * KG Graph API — server-side fetch for the full knowledge graph.
 *
 * Used by the KG Explorer server component page.tsx.
 * Maps the raw API response to GraphData for direct consumption
 * by GraphCanvas.
 *
 * Endpoint: GET /api/v1/kg/graph?limit=<n>
 * Spec: NFM-1336
 */

import { mapSubgraphResponse } from "./materials-api"
import type { KgGraphApiResponse } from "./materials-api"

/** Fetch the full KG graph (server-side only). */
export async function fetchFullGraph(
  limit = 100,
): Promise<KgGraphApiResponse> {
  // NFM-2786: the previous `http://localhost:8000` fallback silently
  // hit Honcho (which occupies host port 8000 in the current stack),
  // returning 404 to the SSR fetch.  Default to the Docker-internal
  // service DNS so SSR calls resolve correctly inside any container.
  // Local dev MUST set API_SERVER_URL explicitly when running the API
  // outside Docker on a non-default host port.
  const backendUrl =
    process.env.API_SERVER_URL ?? "http://nucpot-prod-api:8000"

  const url = `${backendUrl}/api/v1/kg/graph?limit=${limit}`
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: 60 },
  })

  if (!response.ok) {
    throw new Error(
      `Failed to fetch KG graph: ${response.status} ${response.statusText}`,
    )
  }

  const json = await response.json()
  // Backend wraps the payload in { success, data: { nodes, edges } }.
  // Unwrap so mapSubgraphResponse receives nodes/edges at the top level.
  return (json.data ?? json) as KgGraphApiResponse
}

/** Fetch and map full graph to GraphData format. */
export async function fetchFullGraphData(
  limit = 100,
): Promise<ReturnType<typeof mapSubgraphResponse>> {
  const raw = await fetchFullGraph(limit)
  return mapSubgraphResponse(raw)
}
