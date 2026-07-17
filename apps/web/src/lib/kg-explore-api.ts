/**
 * KG Explore API client.
 *
 * Fetches a general graph overview for the KG Explorer page.
 * Uses existing `mapSubgraphResponse` from `materials-api.ts` for
 * mapping the API response to `GraphData`.
 *
 * Backend gap: `GET /api/v1/kg/graph?limit=N` may not exist yet.
 * The function gracefully handles 404/empty responses.
 *
 * Spec: NFM-1376
 */

import { request } from "@/lib/api-client"
import {
  mapSubgraphResponse,
  type KgGraphApiResponse,
} from "@/lib/materials-api"
import type { GraphData } from "@/components/graph/types"

const DEFAULT_LIMIT = 100

const EMPTY_GRAPH_DATA: GraphData = { nodes: [], edges: [] }

/**
 * Fetch a general KG graph overview, mapped to `GraphData` for GraphCanvas.
 *
 * Endpoint: GET /api/v1/kg/graph?limit=N
 *
 * On 404 (endpoint not yet implemented), returns empty GraphData
 * instead of throwing, so the UI can show an empty state.
 */
export async function getKgExploreGraph(
  limit = DEFAULT_LIMIT,
): Promise<GraphData> {
  const sp = new URLSearchParams()
  sp.set("limit", String(limit))

  try {
    const response = await request<KgGraphApiResponse>(
      `/api/v1/kg/graph?${sp.toString()}`,
    )
    return mapSubgraphResponse(response)
  } catch (error: unknown) {
    if (error instanceof Error) {
      const msg = error.message.toLowerCase()
      if (msg.includes("404") || msg.includes("not found")) {
        return EMPTY_GRAPH_DATA
      }
    }
    throw error
  }
}
