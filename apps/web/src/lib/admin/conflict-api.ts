/** API client for the Conflict Resolution admin UI (NFM-2030).
 *
 * Talks to the /api/v1/kg/conflicts endpoints.
 * Uses credentials:'include' for HttpOnly-cookie admin auth.
 */

import type { ApiResponse } from "./hub-types"
import type {
  ConflictListQuery,
  ConflictRecord,
  ConflictResolveRequest,
} from "./conflict-types"

const BASE = "/api/v1/kg/conflicts"

function adminFetch(url: string, init?: RequestInit): Promise<Response> {
  return fetch(url, { ...init, credentials: "include" })
}

function detailToMessage(detail: unknown): string | null {
  if (typeof detail === "string") {
    return detail
  }
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : null,
      )
      .filter((msg): msg is string => Boolean(msg))
    if (msgs.length > 0) {
      return msgs.join("; ")
    }
  }
  return null
}

async function parseEnvelope<T>(
  response: Response,
  action: string,
): Promise<T> {
  if (!response.ok) {
    let message: string | null = null
    try {
      const body: { detail?: unknown; error?: unknown } = await response.json()
      message =
        detailToMessage(body.detail) ??
        (typeof body.error === "string" ? body.error : null)
    } catch {
      // Non-JSON error body
    }
    throw new Error(message ?? `${action}失败 (HTTP ${response.status})`)
  }

  const result: ApiResponse<T> = await response.json()
  if (!result.success) {
    throw new Error(result.error || `${action}失败`)
  }
  return result.data as T
}

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
