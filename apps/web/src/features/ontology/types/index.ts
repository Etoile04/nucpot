/**
 * Ontology management types.
 *
 * Maps to the backend ontology_version API (NFM-2580) and the
 * ontology JSON schema (entity_types / relation_types arrays).
 */

// ── Backend response shapes ──────────────────────────────────────

export type OntologyVersionStatus = 'draft' | 'published' | 'deprecated'

export interface OntologyVersion {
  readonly id: string
  readonly version: string
  readonly status: OntologyVersionStatus
  readonly changelog: string | null
  readonly created_by: string | null
  readonly created_at: string
  readonly updated_at: string
}

export interface PaginatedResponse<T> {
  readonly items: readonly T[]
  readonly total: number
  readonly page: number
  readonly limit: number
  readonly pages: number
}

// ── Ontology JSON schema (inside version.ontology_data) ──────────────

export interface EntityType {
  readonly name: string
  readonly label_template?: string | null
  readonly required_properties?: readonly string[] | null
  readonly description?: string | null
  readonly display_name?: string | null
  readonly domain?: string | null
  readonly chinese_name?: string | null
  readonly english_name?: string | null
}

export interface RelationType {
  readonly name: string
  readonly source_types?: readonly string[] | null
  readonly target_types?: readonly string[] | null
  readonly properties_schema?: Record<string, unknown> | null
  readonly description?: string | null
  readonly display_name?: string | null
}

export interface OntologyData {
  readonly entity_types: readonly EntityType[]
  readonly relation_types: readonly RelationType[]
}

// ── Frontend-only shapes ───────────────────────────────────────────

export type Role = 'curator' | 'admin' | 'reader'

export type FilterStatus = OntologyVersionStatus | 'all'

export interface FilterState {
  readonly status: FilterStatus
  readonly query: string
  readonly page: number
}

export const DEFAULT_FILTER: FilterState = {
  status: 'all',
  query: '',
  page: 1,
}

export const VERSION_STATUSES: readonly OntologyVersionStatus[] = [
  'draft',
  'published',
  'deprecated',
] as const

export const STATUS_LABELS: Record<OntologyVersionStatus, string> = {
  draft: 'Draft',
  published: 'Published',
  deprecated: 'Deprecated',
}

export const STATUS_LABELS_ZH: Record<OntologyVersionStatus, string> = {
  draft: '草稿',
  published: '已发布',
  deprecated: '已废弃',
}
