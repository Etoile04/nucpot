/** API client for the Conflict Resolution admin UI (NFM-2030).
 *
 * Talks to the /api/v1/kg/conflicts endpoints.
 * Uses credentials:'include' for HttpOnly-cookie admin auth.
 */

import type {
  ConflictListQuery,
  ConflictRecord,
  ConflictResolveRequest,
} from "./conflict-types"

import { adminFetch, parseEnvelope } from "./admin-api-utils"

const BASE = "/api/v1/kg/conflicts"

/** GET /api/v1/kg/conflicts — list conflict records. */
export async function listConflicts(
  query: ConflictListQuery = {},
): Promise<ConflictRecord[]> {
  const params = new URLSearchParams()
  if (query.material_id) {
    params.set("material_id", query.material_id)
  }
  if (query.property_type) {
    params.set("property_type", query.property_type)
  }

  const qs = params.toString()
  const url = qs ? `${BASE}?${qs}` : BASE
  const response = await adminFetch(url)
  return parseEnvelope<ConflictRecord[]>(response, "获取冲突列表")
}

/** POST /api/v1/kg/conflicts/{id}/resolve — resolve a conflict. */
export async function resolveConflict(
  conflictId: string,
  body: ConflictResolveRequest,
): Promise<ConflictRecord> {
  const response = await adminFetch(`${BASE}/${conflictId}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  return parseEnvelope<ConflictRecord>(response, "解决冲突")
}
