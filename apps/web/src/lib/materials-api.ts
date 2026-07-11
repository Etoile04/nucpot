/**
 * Materials API client for material property endpoints.
 *
 * Uses the shared `request()` helper from api-client for JWT auth.
 *
 * Spec: NFM-1066 §1
 */

import { request } from "@/lib/api-client"

// ── Types ──────────────────────────────────────────────────────────────

export interface MaterialProperty {
  readonly id: string
  readonly name: string
  readonly value: string
  readonly unit: string | null
  readonly source: string
  readonly confidence: number
}

export interface MaterialPropertyMeta {
  readonly total: number
  readonly page: number
  readonly limit: number
}

export interface MaterialPropertyListResponse {
  readonly data: ReadonlyArray<MaterialProperty>
  readonly meta: MaterialPropertyMeta
}

export interface MaterialPropertyListParams {
  readonly page?: number
  readonly limit?: number
  readonly sort?: string
  readonly order?: "asc" | "desc"
  readonly filter?: string
}

export interface MaterialSummary {
  readonly id: string
  readonly name: string
  readonly formula: string | null
}

// ── API functions ─────────────────────────────────────────────────────

/**
 * Fetch paginated properties for a given material.
 */
export async function getMaterialProperties(
  materialId: string,
  params: MaterialPropertyListParams = {},
): Promise<MaterialPropertyListResponse> {
  const sp = new URLSearchParams()

  sp.set("page", String(params.page ?? 1))
  sp.set("limit", String(params.limit ?? 50))
  if (params.sort) sp.set("sort", params.sort)
  if (params.order) sp.set("order", params.order)
  if (params.filter) sp.set("filter", params.filter)

  return request<MaterialPropertyListResponse>(
    `/api/v1/materials/${materialId}/properties?${sp.toString()}`,
  )
}

/**
 * Fetch a material summary by ID.
 */
export async function getMaterial(
  materialId: string,
): Promise<MaterialSummary> {
  return request<MaterialSummary>(
    `/api/v1/materials/${materialId}`,
  )
}
