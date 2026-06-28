/** API client for reference gaps endpoints. */

import type {
  ApiResponse,
  FillRequest,
  FillResponse,
  ReferenceGapsSummaryResponse,
} from "./types"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

/**
 * Get reference gaps summary statistics.
 */
export async function getGapsSummary(): Promise<ReferenceGapsSummaryResponse> {
  const response = await fetch(`${API_BASE}/api/v1/reference-gaps/summary`)

  if (!response.ok) {
    throw new Error(`Failed to fetch gaps summary: ${response.statusText}`)
  }

  const result: ApiResponse<ReferenceGapsSummaryResponse> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error || "Failed to fetch gaps summary")
  }

  return result.data
}

/**
 * Trigger fill operation for a specific gap tuple.
 */
export async function fillGap(
  payload: FillRequest,
): Promise<FillResponse> {
  const response = await fetch(`${API_BASE}/api/v1/reference-gaps/fill`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    throw new Error(`Failed to fill gap: ${response.statusText}`)
  }

  const result: ApiResponse<FillResponse> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error || "Failed to fill gap")
  }

  return result.data
}
