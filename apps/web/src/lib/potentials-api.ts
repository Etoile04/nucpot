/**
 * API client for potential endpoints.
 *
 * Uses same-origin Next.js API routes (BFF pattern) that query Supabase
 * directly. This avoids the need for a separate Python API server in
 * serverless deployments (Vercel).
 */

/** Verification lifecycle written by nucpot-autovc (see docs/verification-contract.md). */
export type VerificationStatus = "unverified" | "pending" | "verified" | "failed"

export interface PotentialSummary {
  id: string
  name: string
  display_name?: string
  type: string
  format?: string
  elements: string[]
  description?: string
  version: string
  tags: string[]
  file_url?: string
}

export interface PotentialDetail extends PotentialSummary {
  subtype?: string
  system_name?: string
  system_tags: string[]
  applicability: Record<string, unknown>
  references: Record<string, unknown>[]
  developers: Record<string, unknown>[]
  verified_props: Record<string, unknown> | null
  sim_software: string[]
  lammps_config: Record<string, unknown>
  file_hash?: string
  file_size?: number
  source?: string
  source_doi?: string
  license?: string
  extra: Record<string, unknown>
  verification_status: VerificationStatus
}

export interface PotentialListResult {
  potentials: PotentialSummary[]
  total: number
  page: number
  limit: number
  total_pages: number
}

/** Types for potential browse/search/detail endpoints. */

export interface ListParams {
  type?: string
  elements?: string[]
  q?: string
  page?: number
  limit?: number
  sort?: "updated" | "name" | "type"
}

/**
 * NFM-4311 — list loading resilience.
 *
 * One automatic retry (with a short backoff) for transient failures:
 * network errors (the 2026-09-04 "Failed to fetch" interruptions), 5xx
 * and 429. Definitive 4xx responses fail immediately — retrying a 404/422
 * cannot succeed and would only delay the error state. Manual retry is
 * layered on top by the views (重试 button), so the failure state always
 * carries a recovery action.
 */
const AUTO_RETRIES = 1
const RETRY_BACKOFF_MS = 400
const REQUEST_TIMEOUT_MS = 15_000

function timeoutSignal(): AbortSignal | undefined {
  return typeof AbortSignal !== "undefined" && "timeout" in AbortSignal
    ? AbortSignal.timeout(REQUEST_TIMEOUT_MS)
    : undefined
}

async function fetchList(sp: URLSearchParams): Promise<PotentialListResult> {
  const url = `/api/potentials?${sp.toString()}`
  let lastError: Error = new Error("Failed to list potentials")

  for (let attempt = 0; attempt <= AUTO_RETRIES; attempt++) {
    if (attempt > 0) {
      await new Promise((resolve) => setTimeout(resolve, RETRY_BACKOFF_MS))
    }
    let response: Response
    try {
      response = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        signal: timeoutSignal(),
      })
    } catch (error) {
      // Network failure (interrupted fetch, DNS, timeout) — retryable.
      lastError = error instanceof Error ? error : new Error(String(error))
      continue
    }
    if (response.ok) {
      // The BFF route returns data directly (not wrapped in ApiResponse)
      return (await response.json()) as PotentialListResult
    }
    lastError = new Error(`Failed to list potentials: ${response.status}`)
    const retryableStatus = response.status >= 500 || response.status === 429
    if (!retryableStatus) throw lastError // definitive 4xx — fail fast
  }
  throw lastError
}

export async function listPotentials(params: ListParams = {}): Promise<PotentialListResult> {
  const sp = new URLSearchParams()
  if (params.type) sp.set("type", params.type)
  if (params.elements?.length) sp.set("elements", params.elements.join(","))
  if (params.q) sp.set("q", params.q)
  sp.set("page", String(params.page ?? 1))
  sp.set("limit", String(params.limit ?? 20))
  sp.set("sort", params.sort ?? "updated")
  return fetchList(sp)
}

export async function getPotential(id: string): Promise<PotentialDetail> {
  const response = await fetch(`/api/potentials/${id}`, {
    headers: { "Content-Type": "application/json" },
  })
  if (!response.ok) throw new Error(`Failed to fetch potential: ${response.status}`)
  return (await response.json()) as PotentialDetail
}
