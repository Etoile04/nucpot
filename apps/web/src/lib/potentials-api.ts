/** API client for potential endpoints. Follows feedback-api.ts pattern. */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"

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

interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
}

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
  const response = await fetch(`${API_BASE}/api/v1/potentials?${sp.toString()}`, {
    headers: { "Content-Type": "application/json" },
  })
  if (!response.ok) throw new Error(`Failed to list potentials: ${response.status}`)
  const json: ApiResponse<PotentialListResult> = await response.json()
  if (!json.success || !json.data) throw new Error(json.error ?? "Unknown error")
  return json.data
}

export async function getPotential(id: string): Promise<PotentialDetail> {
  const response = await fetch(`${API_BASE}/api/v1/potentials/${id}`, {
    headers: { "Content-Type": "application/json" },
  })
  if (!response.ok) throw new Error(`Failed to fetch potential: ${response.status}`)
  const json: ApiResponse<PotentialDetail> = await response.json()
  if (!json.success || !json.data) throw new Error(json.error ?? "Unknown error")
  return json.data
}
