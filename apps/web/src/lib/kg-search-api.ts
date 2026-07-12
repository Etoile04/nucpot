/**
 * Typed API client for KG Search endpoints.
 *
 * Endpoint:
 *   GET  /api/v1/kg/search?q=...&type=...&status=active&limit=20&offset=0
 *
 * Public read-only endpoint (no auth required), but auth headers are
 * forwarded when available for future rate-limit or personalization.
 */

import { getToken } from '@/lib/api-client'

// ── Types ───────────────────────────────────────────────────────────

export interface KgSearchItem {
  readonly id: string
  readonly node_type: string
  readonly label: string
  readonly aliases: readonly string[]
  readonly properties: Record<string, unknown>
  readonly confidence: number
  readonly status: string
  readonly source_id: string | null
}

export interface KgSearchResponse {
  readonly items: readonly KgSearchItem[]
  readonly total: number
  readonly limit: number
  readonly offset: number
}

// ── Node type filter options (must match backend VALID_NODE_TYPES) ──

export const KG_NODE_TYPES = [
  'Material',
  'Property',
  'Experiment',
  'Condition',
  'Publication',
] as const

export type KgNodeType = (typeof KG_NODE_TYPES)[number]

// ── Helpers ─────────────────────────────────────────────────────────

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  const token = getToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  return headers
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? body.message ?? `API error: ${res.status}`)
  }
  return res.json() as Promise<T>
}

// ── API ─────────────────────────────────────────────────────────────

export interface KgSearchParams {
  readonly q?: string
  readonly type?: string
  readonly limit?: number
  readonly offset?: number
}

export function fetchKgSearch(
  params: KgSearchParams = {},
): Promise<KgSearchResponse> {
  const sp = new URLSearchParams()

  if (params.q) sp.set('q', params.q)
  if (params.type) sp.set('type', params.type)
  sp.set('status', 'active')

  const limit = params.limit ?? 20
  const offset = params.offset ?? 0
  sp.set('limit', String(limit))
  sp.set('offset', String(offset))

  const qs = sp.toString()
  return request<KgSearchResponse>(
    `/api/v1/kg/search${qs ? `?${qs}` : ''}`,
    { headers: authHeaders() },
  )
}
