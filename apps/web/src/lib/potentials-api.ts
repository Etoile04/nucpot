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

export async function listPotentials(params: ListParams = {}): Promise<PotentialListResult> {
  const sp = new URLSearchParams()
  if (params.type) sp.set("type", params.type)
  if (params.elements?.length) sp.set("elements", params.elements.join(","))
  if (params.q) sp.set("q", params.q)
  sp.set("page", String(params.page ?? 1))
  sp.set("limit", String(params.limit ?? 20))
  sp.set("sort", params.sort ?? "updated")
  const response = await fetch(`/api/potentials?${sp.toString()}`, {
    headers: { "Content-Type": "application/json" },
  })
  if (!response.ok) throw new Error(`Failed to list potentials: ${response.status}`)
  // The BFF route returns data directly (not wrapped in ApiResponse)
  return (await response.json()) as PotentialListResult
}

export async function getPotential(id: string): Promise<PotentialDetail> {
  const response = await fetch(`/api/potentials/${id}`, {
    headers: { "Content-Type": "application/json" },
  })
  if (!response.ok) throw new Error(`Failed to fetch potential: ${response.status}`)
  return (await response.json()) as PotentialDetail
}
