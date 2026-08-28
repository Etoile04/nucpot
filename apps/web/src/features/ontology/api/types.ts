/**
 * Shared types for the ontology management API.
 * Mirrors backend schemas from OntologyVersionRead + PaginatedResponse.
 */

export type OntologyVersionStatus = "draft" | "published" | "deprecated"

export interface OntologyVersion {
  readonly id: string
  readonly version: string
  readonly status: OntologyVersionStatus
  readonly changelog: string | null
  readonly created_by: string
  readonly created_at: string
  readonly ontology_data: Record<string, unknown> | null
}

export interface PaginatedResponse<T> {
  readonly items: readonly T[]
  readonly total: number
  readonly page: number
  readonly limit: number
  readonly pages: number
}

export interface OntologyVersionListParams {
  readonly page?: number
  readonly limit?: number
  readonly status?: OntologyVersionStatus | "all"
}

export interface CreateDraftPayload {
  readonly changelog?: string
  readonly ontology_data?: Record<string, unknown>
}

export interface PublishPayload {
  readonly changelog: string
  readonly bump?: "patch" | "minor" | "major"
}

/**
 * Count entity/relation types from ontology_data JSON structure.
 */
export function extractCounts(
  data: Record<string, unknown> | null | undefined,
): Readonly<{ entityCount: number; relationCount: number }> {
  if (!data) return { entityCount: 0, relationCount: 0 }
  const entityTypes = data["entity_types"] as readonly unknown[] | undefined
  const relationTypes = data["relation_types"] as readonly unknown[] | undefined
  return {
    entityCount: entityTypes?.length ?? 0,
    relationCount: relationTypes?.length ?? 0,
  }
}
