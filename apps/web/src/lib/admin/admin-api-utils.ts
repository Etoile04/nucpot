/** Shared utilities for all admin API clients.
 *
 * Centralises credentials:'include' fetch, FastAPI error parsing,
 * and ApiResponse envelope unwrapping — used by hub-api, conflict-api,
 * reference-data-api, and any future admin API modules.
 */

import type { ApiResponse } from "./hub-types"

/** Wrapped fetch with credentials:'include' for all admin API calls. */
export function adminFetch(url: string, init?: RequestInit): Promise<Response> {
  return fetch(url, { ...init, credentials: "include" })
}

/** Render FastAPI error payloads (string or 422 array) as one message. */
export function detailToMessage(detail: unknown): string | null {
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

/** Parse a hub API response, unwrapping the ApiResponse envelope. */
export async function parseEnvelope<T>(
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
      // Non-JSON error body — fall through to the generic message.
    }
    throw new Error(message ?? `${action}失败 (HTTP ${response.status})`)
  }

  const result: ApiResponse<T> = await response.json()
  if (!result.success) {
    throw new Error(result.error || `${action}失败`)
  }
  return result.data as T
}
