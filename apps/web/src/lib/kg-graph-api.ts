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
  const DOCKER_INTERNAL_API = "http://nucpot-prod-api:8000"
  const backendUrl =
    process.env.API_SERVER_URL ?? DOCKER_INTERNAL_API

  if (!process.env.API_SERVER_URL) {
    console.warn(
      "[kg-graph-api] API_SERVER_URL is not set — SSR fetch targets " +
      "the Docker-internal service DNS (nucpot-prod-api:8000). " +
      "For local dev outside Docker, set API_SERVER_URL explicitly.",
    )
  }

  const url = `${backendUrl}/api/v1/kg/graph?limit=${limit}`

  try {
    const response = await fetch(url, {
      headers: { Accept: "application/json" },
      next: { revalidate: 60 },
    })

    if (!response.ok) {
      throw new Error(
        `KG graph API returned ${response.status} ${response.statusText}` +
        ` for ${url}`,
      )
    }

    const json = await response.json()
    // Backend wraps the payload in { success, data: { nodes, edges } }.
    // Unwrap so mapSubgraphResponse receives nodes/edges at the top level.
    return (json.data ?? json) as KgGraphApiResponse
  } catch (error: unknown) {
    // Distinguish Docker DNS unreachability from backend-down errors
    // to aid local dev debugging (NFM-2786 review item 4).
    const message = error instanceof Error ? error.message : String(error)
    const code = (error as { code?: string }).code

    if (code === "ENOTFOUND" && backendUrl.includes("nucpot-prod-api")) {
      throw new Error(
        `[kg-graph-api] Docker service DNS "nucpot-prod-api" is not resolvable. ` +
        `Are you running outside Docker? Set API_SERVER_URL explicitly ` +
        `(e.g. API_SERVER_URL=http://localhost:8001). Original: ${message}`,
      )
    }

    if (code === "ECONNREFUSED") {
      throw new Error(
        `[kg-graph-api] Backend refused connection at ${backendUrl}. ` +
        `The API server may not be running. Original: ${message}`,
      )
    }

    throw error
  }
}

/** Fetch and map full graph to GraphData format. */
export async function fetchFullGraphData(
  limit = 100,
): Promise<ReturnType<typeof mapSubgraphResponse>> {
  const raw = await fetchFullGraph(limit)
  return mapSubgraphResponse(raw)
}
