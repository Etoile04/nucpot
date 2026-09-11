/**
 * Canonical RAG API contract — single source of truth for field names.
 *
 * NFM-1848: This file defines the canonical response/request shapes for the
 * three LightRAG endpoints. Every field name here MUST match the
 * corresponding Pydantic schema in the backend:
 *
 *   - QueryResponse  → apps/api/src/nfm_db/schemas/lightrag.py:116
 *   - IngestResponse → apps/api/src/nfm_db/schemas/lightrag.py:64
 *   - HealthResponse → apps/api/src/nfm_db/schemas/lightrag.py:144
 *   - IngestRequest  → apps/api/src/nfm_db/schemas/lightrag.py:39
 *   - QueryRequest   → apps/api/src/nfm_db/schemas/lightrag.py:90
 *
 * Frontend components MUST NOT consume backend field names directly.
 * Instead, rag-api.ts maps these canonical backend fields to
 * frontend-facing types (RagQueryResponse, RagMessage, etc.).
 *
 * Contract ownership:
 *   - Backend Pydantic schemas are the authoritative definition.
 *   - This file is the TypeScript mirror — keep in sync.
 *   - rag-api.ts performs the backend → frontend field mapping.
 */

// ---------------------------------------------------------------------------
// Query endpoint — POST /api/v1/lightrag/query
// ---------------------------------------------------------------------------

/** Mirrors nfm_db.schemas.lightrag.QueryMode */
export type RagQueryMode =
  | "naive"
  | "local"
  | "global"
  | "hybrid"
  | "mix"

/** Mirrors nfm_db.schemas.lightrag.QueryRequest */
export interface RagContractQueryRequest {
  readonly query: string
  readonly mode: RagQueryMode
  readonly include_references: boolean
}

/** A single source reference returned by LightRAG. */
export interface RagContractReference {
  readonly reference_id?: string | number
  readonly file_path?: string
  readonly content?: string | null
}

/** Mirrors nfm_db.schemas.lightrag.QueryResponse. */
export interface RagContractQueryResponse {
  /** Generated answer to the query. */
  readonly response: string
  /** Source references from the knowledge graph. */
  readonly references: readonly RagContractReference[]
  /** KG entities related to the query. */
  readonly entities: readonly Record<string, unknown>[]
  /** KG relationships related to the query. */
  readonly relationships: readonly Record<string, unknown>[]
  /**
   * NFM-4539 RAG-B / AC-4: transparent degradation envelope.  When
   * ``used=true`` the semantic path stalled and the answer was rescued
   * via the ILIKE full-text path; the UI surfaces a badge so the user
   * knows the answer is degraded.
   */
  readonly fallback: RagContractFallback
}

/**
 * Mirrors nfm_db.schemas.lightrag.FallbackInfo.  ``kind`` is the
 * fallback family (``"iliKE"`` today); ``reason`` is the first-class
 * machine-readable code from NFM-4734 AC-2; ``original_error`` carries
 * the upstream failure message so logs can correlate the UI badge
 * with the access_log row.
 *
 * NFM-4734 reason-code contract:
 *   - "none"            — steady state, no fallback fired
 *   - "semantic_timeout" — LightRAG sidecar exceeded its 10s budget
 *   - "semantic_empty"   — LightRAG answered with zero references
 *   - "provider_error"   — defensive safety-net path
 *   - legacy: "iliKE" / "ilike" / unknown string — render as timeout-ish
 */
export type RagFallbackReason =
  | "none"
  | "semantic_timeout"
  | "semantic_empty"
  | "provider_error"
  | (string & {}) // accept legacy / future codes without breaking clients

export interface RagContractFallback {
  readonly used: boolean
  readonly kind: string | null
  /**
   * Optional first-class machine-readable code (NFM-4734 AC-2).
   * Pre-4734 servers do not emit the field; the frontend default is
   * ``"none"`` so legacy clients keep rendering the success path.
   */
  readonly reason?: RagFallbackReason
  readonly original_error: string | null
}

// ---------------------------------------------------------------------------
// Ingest endpoint — POST /api/v1/lightrag/ingest
// ---------------------------------------------------------------------------

/** Mirrors nfm_db.schemas.lightrag.IngestRequest. */
export interface RagContractIngestRequest {
  readonly text: string
  readonly file_source?: string | null
}

/** Mirrors nfm_db.schemas.lightrag.IngestResponse. */
export interface RagContractIngestResponse {
  readonly status: string
  readonly message: string
  readonly track_id?: string | null
}

// ---------------------------------------------------------------------------
// Health endpoint — GET /api/v1/lightrag/health
// ---------------------------------------------------------------------------

/** Mirrors nfm_db.schemas.lightrag.HealthResponse. */
export interface RagContractHealthResponse {
  readonly status: string
  readonly error?: string | null
  readonly active_provider: string
  readonly fallback_active: boolean
  readonly lightrag_version?: string | null
}

// ---------------------------------------------------------------------------
// API envelope (shared across all endpoints)
// ---------------------------------------------------------------------------

/** Mirrors nfm_db.schemas.common.ApiResponse<T>. */
export interface RagContractApiResponse<T> {
  readonly success: boolean
  readonly data: T
  readonly error?: string
}
