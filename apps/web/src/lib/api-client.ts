/**
 * Shared API client for backend communication.
 *
 * Authentication uses HttpOnly cookies set by the server.
 * All requests include credentials:"include" to send cookies automatically.
 * No localStorage token management needed (XSS-safe).
 *
 * NFM-2255: On 401, the client automatically attempts a silent token
 * refresh (POST /api/v1/auth/refresh) before retrying the original
 * request exactly once.  Concurrent 401s share a single in-flight
 * refresh to avoid thundering-herd problems (AC: "N concurrent 401s
 * => exactly 1 refresh fired").
 */

/**
 * Thrown for non-OK HTTP responses. Carries the numeric status so callers
 * can classify the error without parsing the message text — see
 * `uploadErrorStatus` and NFM-3359 (AC-3).
 */
export class ApiError extends Error {
  readonly status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

/**
 * Legacy JSON-body shape returned by the backend for non-OK responses.
 * Used internally by `request()` and `literatureApi.upload()` to extract
 * the server's `detail` / `message` field before constructing an
 * `ApiError`. Internal-only — external callers should consume `ApiError`.
 */
interface ApiErrorBody {
  readonly detail?: string
  readonly message?: string
}

/**
 * Extract the HTTP status from an unknown thrown value.
 *
 * Returns the numeric status for `ApiError` instances (and any other value
 * with a numeric `status` property); returns `null` for network errors,
 * plain `Error`s, and non-error values. Use this instead of inspecting the
 * message body — message text may contain user-controlled content that
 * happens to include digits matching other status codes (NFM-3359 AC-3:
 * e.g. a 413 detail "File too large: 54031234 bytes (max 52428800)" must
 * not be classified as 403 just because the byte count contains "403").
 */
export function uploadErrorStatus(err: unknown): number | null {
  if (err instanceof ApiError) return err.status
  if (
    err !== null &&
    typeof err === "object" &&
    "status" in err &&
    typeof (err as { status: unknown }).status === "number"
  ) {
    return (err as { status: number }).status
  }
  return null
}

function buildHeaders(custom?: Record<string, string>): HeadersInit {
  return {
    "Content-Type": "application/json",
    ...custom,
  }
}

// ── 401 refresh interceptor (NFM-2255) ──────────────────────────────

/** Singleton in-flight refresh promise — ensures exactly one refresh. */
let inFlightRefresh: Promise<boolean> | null = null

/**
 * Attempt to refresh the session token.  Returns true on success,
 * false if the refresh itself failed (e.g. revoked token).
 *
 * Concurrent callers share the same in-flight request — the
 * deduplication guarantees the NFM-2236 AC: "N concurrent 401s
 * => exactly 1 refresh fired".
 */
async function attemptRefresh(): Promise<boolean> {
  if (inFlightRefresh) return inFlightRefresh

  inFlightRefresh = (async () => {
    try {
      const res = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
      })
      return res.ok
    } catch {
      return false
    } finally {
      inFlightRefresh = null
    }
  })()

  return inFlightRefresh
}

/**
 * Generic request wrapper.
 * Throws on non-OK responses with a descriptive error message.
 *
 * 401 handling: on first 401, silently refreshes the token and retries
 * the request once.  If the refresh fails or the retry also 401s, throws
 * the original error message so the UI can prompt re-authentication.
 */
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    credentials: "include",
    headers: buildHeaders(
      options.headers as Record<string, string> | undefined,
    ),
  })

  if (response.status === 401) {
    const refreshed = await attemptRefresh()
    if (refreshed) {
      // Retry the original request exactly once after successful refresh.
      const retry = await fetch(path, {
        ...options,
        credentials: "include",
        headers: buildHeaders(
          options.headers as Record<string, string> | undefined,
        ),
      })
      if (retry.ok) {
        if (retry.status === 204) return undefined as T
        return retry.json() as Promise<T>
      }
    }
    // Refresh failed or retry also 401 — surface the original message.
    throw new Error("认证已过期，请重新登录后重试")
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiErrorBody | null
    const message = body?.detail ?? body?.message ?? `请求失败 (${response.status})`
    throw new ApiError(message, response.status)
  }

  // 204 No Content
  if (response.status === 204) {
    return undefined as T
  }

  return response.json() as Promise<T>
}

/** Auth endpoints */

export interface TokenResponse {
  readonly access_token: string
  readonly token_type: string
}

export interface UserProfile {
  readonly id: string
  readonly username: string
  readonly email: string
  readonly full_name: string | null
  readonly blog_role: string | null
  readonly is_active: boolean
}

/**
 * Standard backend envelope shape `ApiResponse<T> = { success, data: T, error? }`.
 * The shared `request()` helper returns the raw envelope (it does NOT
 * auto-unwrap `.data`), so callers that need the inner payload should type
 * the request as `request<ApiResponse<T>>` and read `.data` themselves.
 */
export interface ApiResponse<T> {
  readonly success: boolean
  readonly data: T
}

export const authApi = {
  login: async (username: string, password: string): Promise<TokenResponse> => {
    const body = new URLSearchParams()
    body.append("username", username)
    body.append("password", password)

    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
      credentials: "include",
    })

    if (response.status === 401) {
      throw new Error("用户名或密码错误")
    }

    if (!response.ok) {
      throw new Error("登录失败，请稍后重试")
    }

    // Cookie is set automatically by the server (Set-Cookie header)
    return response.json() as Promise<TokenResponse>
  },

  getMe: (): Promise<UserProfile> =>
    request<ApiResponse<UserProfile>>("/api/v1/auth/me").then((r) => r.data),

  logout: async (): Promise<void> => {
    await fetch("/api/v1/auth/logout", {
      method: "POST",
      credentials: "include",
    }).catch(() => {})
  },
} as const

/** Blog post types matching backend BlogPostResponse + file content */

export interface BlogPostResponse {
  readonly id: string
  readonly slug: string
  readonly title: string
  readonly status: string
  readonly author_id: string
  readonly reviewer_id: string | null
  readonly reviewed_at: string | null
  readonly published_at: string | null
  readonly rejection_reason: string | null
  readonly created_at: string
  readonly updated_at: string
  readonly content?: string
  readonly summary?: string
  readonly tags?: readonly string[]
  readonly author_name?: string
}

export interface WorkflowActionResponse {
  readonly id: string
  readonly slug: string
  readonly status: string
  readonly message: string
}

/** Blog API */

interface BlogPostCreatePayload {
  readonly title: string
  readonly content: string
  readonly summary: string
  readonly tags: readonly string[]
  readonly author_name: string
}

interface BlogPostUpdatePayload {
  readonly title?: string
  readonly content?: string
  readonly summary?: string
  readonly tags?: readonly string[]
  readonly author_name?: string
}

export const blogApi = {
  list: (params?: { status?: string; limit?: number; offset?: number }): Promise<readonly BlogPostResponse[]> => {
    const query = new URLSearchParams()
    if (params?.status) query.set("status", params.status)
    if (params?.limit) query.set("limit", String(params.limit))
    if (params?.offset) query.set("offset", String(params.offset))
    const qs = query.toString()
    return request<readonly BlogPostResponse[]>(`/api/v1/admin/blog/posts${qs ? `?${qs}` : ""}`)
  },

  get: (slug: string): Promise<BlogPostResponse> =>
    request<BlogPostResponse>(`/api/v1/admin/blog/posts/${slug}`),

  create: (payload: BlogPostCreatePayload): Promise<BlogPostResponse> =>
    request<BlogPostResponse>("/api/v1/admin/blog/posts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  update: (slug: string, payload: BlogPostUpdatePayload): Promise<BlogPostResponse> =>
    request<BlogPostResponse>(`/api/v1/admin/blog/posts/${slug}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  delete: (slug: string): Promise<void> =>
    request<void>(`/api/v1/admin/blog/posts/${slug}`, {
      method: "DELETE",
    }),

  workflow: (
    slug: string,
    action: string,
    rejectionReason?: string,
  ): Promise<WorkflowActionResponse> =>
    request<WorkflowActionResponse>(
      `/api/v1/admin/blog/posts/${slug}/workflow`,
      {
        method: "POST",
        body: JSON.stringify({
          action,
          rejection_reason: rejectionReason,
        }),
      },
    ),
} as const

// ─── V1 Extraction API types ──────────────────────────────────────

export type ExtractionSourceType = "doi" | "url" | "file" | "internal_id"

/** V1 extraction job lifecycle statuses (mirrors backend JobStatus StrEnum). */
export type V1JobStatus =
  | "queued"
  | "running"
  | "extracting"
  | "mapping"
  | "quality_gate"
  | "completed"
  | "partial"
  | "failed"

export interface ExtractionTriggerRequest {
  readonly source_reference: string
  readonly source_type: ExtractionSourceType
  readonly element_systems?: readonly string[]
  readonly cache_level?: string
  readonly max_confidence?: string
}

export interface ExtractionTriggerResponse {
  readonly job_id: string
  readonly source_reference: string
  readonly source_type: ExtractionSourceType
  readonly status: V1JobStatus
  readonly message: string
}

export interface ExtractionStatusResponse {
  readonly job_id: string
  readonly source_reference: string
  readonly source_type: ExtractionSourceType
  readonly status: V1JobStatus
  readonly extracted_count: number
  readonly staged_count: number
  readonly rejected_count: number
  readonly error_message?: string | null
  readonly created_at?: string | null
  readonly started_at?: string | null
  readonly completed_at?: string | null
}

// ─── V1 Extraction API ───────────────────────────────────────────

/** Internal envelope for v1 extraction endpoints. */
interface ExtractionEnvelope<T> {
  readonly success: boolean
  readonly data: T
}

export const extractionApi = {
  /** POST /api/v1/extraction/trigger — Trigger extraction for a literature source */
  trigger: async (
    payload: ExtractionTriggerRequest,
  ): Promise<ExtractionTriggerResponse> => {
    const envelope = await request<ExtractionEnvelope<ExtractionTriggerResponse>>(
      "/api/v1/extraction/trigger",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    )
    return envelope.data
  },

  /** GET /api/v1/extraction/status/{jobId} — Check extraction job status */
  getStatus: async (
    jobId: string,
  ): Promise<ExtractionStatusResponse> => {
    const envelope = await request<ExtractionEnvelope<ExtractionStatusResponse>>(
      `/api/v1/extraction/status/${jobId}`,
    )
    return envelope.data
  },
} as const

// ─── Literature Management API (V1 extraction pipeline entry) ────────

/** Lifecycle states for a literature item (mirrors backend parse_status). */
export type LiteratureStatus =
  | "uploaded"
  | "parsing"
  | "extracting"
  | "completed"
  | "failed"
  | (string & {})

export interface LiteratureListItem {
  readonly id: string
  readonly title: string
  readonly doi: string | null
  readonly journal: string | null
  readonly year: number | null
  readonly abstract?: string | null
  readonly status: LiteratureStatus
  readonly source_id: string | null
  readonly created_at: string
}

export interface LiteratureFigure {
  readonly id: string
  readonly page_number?: number | null
  readonly figure_type?: string | null
  readonly image_path?: string | null
  readonly caption?: string | null
  readonly confidence?: number
}

/** Origin discriminator for extraction result items (mirrors backend ExtractionSourceType). */
export type LiteratureExtractionSourceType = "manual" | "kg_node" | "kg_edge"

/** One extraction result row with source-type discriminator. */
export interface LiteratureExtractionResultItem {
  readonly id: string
  readonly source_type: LiteratureExtractionSourceType
  readonly property_name: string
  readonly item_type: string
  readonly item_data?: Record<string, unknown>
  readonly value?: unknown
  readonly confidence?: number | null
  readonly created_at?: string | null
  readonly review_status?: string | null
  readonly unit?: string | null
  readonly source_page?: number | null
  readonly source_paragraph?: string | null
  readonly source_node_id?: string | null
  readonly source_target_id?: string | null
  /** NFM-2247: how this item was produced (e.g. ["llm"], ["manual"], ["llm","manual"]). */
  readonly provenance?: readonly string[]
}

export interface LiteratureDetail extends LiteratureListItem {
  readonly content_md?: string | null
  readonly figures?: readonly LiteratureFigure[]
  readonly extraction_results?: readonly LiteratureExtractionResultItem[]
  readonly updated_at?: string | null
}

export interface LiteratureUploadResponse {
  readonly literature_id: string
  readonly status: LiteratureStatus
}

export interface LiteratureStatusResponse {
  readonly id: string
  readonly status: LiteratureStatus
  readonly progress: number
  readonly error?: string | null
}

export interface LiteratureReextractResponse {
  readonly id: string
  readonly status: LiteratureStatus
  readonly message?: string
}

interface LiteratureListEnvelope {
  readonly success: boolean
  readonly data: {
    readonly items: readonly LiteratureListItem[]
    readonly total: number
    readonly page: number
    readonly limit: number
    readonly pages: number
  }
}

interface LiteratureDetailEnvelope {
  readonly success: boolean
  readonly data: LiteratureDetail
}

interface LiteratureUploadEnvelope {
  readonly success: boolean
  readonly data: LiteratureUploadResponse
}

interface LiteratureReextractEnvelope {
  readonly success: boolean
  readonly data: LiteratureReextractResponse
}

interface LiteratureDeleteEnvelope {
  readonly success: boolean
  readonly data: { readonly message: string }
}

interface DoiRequest {
  readonly doi: string
}

interface LiteratureListParams {
  readonly page?: number
  readonly limit?: number
  readonly search?: string
  readonly status?: string
  readonly yearMin?: number
  readonly yearMax?: number
}

function buildLiteratureListQuery(p: LiteratureListParams): string {
  const qs = new URLSearchParams()
  if (p.page) qs.set("page", String(p.page))
  if (p.limit) qs.set("limit", String(p.limit))
  if (p.search) qs.set("search", p.search)
  if (p.status) qs.set("status", p.status)
  if (p.yearMin !== undefined) qs.set("year_min", String(p.yearMin))
  if (p.yearMax !== undefined) qs.set("year_max", String(p.yearMax))
  const s = qs.toString()
  return s ? `?${s}` : ""
}

export const literatureApi = {
  /** GET /api/v1/literature — paginated list with filters */
  list: async (params: LiteratureListParams = {}): Promise<{
    readonly items: readonly LiteratureListItem[]
    readonly total: number
  }> => {
    const qs = buildLiteratureListQuery(params)
    const env = await request<LiteratureListEnvelope>(`/api/v1/literature${qs}`)
    return { items: env.data.items, total: env.data.total }
  },

  /** GET /api/v1/literature/search?q= — full-text search */
  search: async (
    q: string,
    page: number = 1,
    limit: number = 20,
  ): Promise<LiteratureListItem[]> => {
    const env = await request<LiteratureListEnvelope>(
      `/api/v1/literature/search?q=${encodeURIComponent(q)}&page=${page}&limit=${limit}`,
    )
    return env.data.items as LiteratureListItem[]
  },

  /** GET /api/v1/literature/{id} — full detail + extraction results */
  get: async (id: string): Promise<LiteratureDetail> => {
    const env = await request<LiteratureDetailEnvelope>(`/api/v1/literature/${id}`)
    return env.data
  },

  /** GET /api/v1/literature/{id}/status — processing status */
  getStatus: async (id: string): Promise<LiteratureStatusResponse> => {
    const env = await request<{
      success: boolean
      data: LiteratureStatusResponse
    }>(`/api/v1/literature/${id}/status`)
    return env.data
  },

  /** POST /api/v1/literature/upload — multipart upload of a PDF file */
  upload: async (file: File): Promise<LiteratureUploadResponse> => {
    const formData = new FormData()
    formData.append("file", file)
    const response = await fetch("/api/v1/literature/upload", {
      method: "POST",
      body: formData,
      credentials: "include",
    })
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as ApiErrorBody | null
      // NFM-3359 AC-3: throw ApiError so the UI can classify by status code
      // (e.g. 403 → permission toast) instead of substring-matching the
      // message body, which would mis-classify a 413 whose byte count
      // happens to contain "403" as a permission error.
      throw new ApiError(
        body?.detail ?? `上传失败 (${response.status})`,
        response.status,
      )
    }
    const env = (await response.json()) as LiteratureUploadEnvelope
    return env.data
  },

  /** POST /api/v1/literature/from-doi — fetch paper by DOI */
  fromDoi: async (doi: string): Promise<LiteratureUploadResponse> => {
    const env = await request<LiteratureUploadEnvelope>(
      "/api/v1/literature/from-doi",
      {
        method: "POST",
        body: JSON.stringify({ doi } satisfies DoiRequest),
      },
    )
    return env.data
  },

  /** POST /api/v1/literature/{id}/reextract — trigger re-extraction */
  reextract: async (id: string): Promise<LiteratureReextractResponse> => {
    const env = await request<LiteratureReextractEnvelope>(
      `/api/v1/literature/${id}/reextract`,
      { method: "POST" },
    )
    return env.data
  },

  /** DELETE /api/v1/literature/{id} — delete literature + associated data */
  delete: async (id: string): Promise<{ readonly message: string }> => {
    const env = await request<LiteratureDeleteEnvelope>(
      `/api/v1/literature/${id}`,
      { method: "DELETE" },
    )
    return env.data
  },
} as const

// ─── V4 Extraction API re-exports ───────────────────────────────

export {
  submitExtractionJob,
  getExtractionStatus,
  getExtractionResults,
  browseProperties,
  validateExtractionResults,
  getMaterialSystems,
} from "./v4-extraction/api"

export type {
  V4ExtractionSubmitRequest,
  V4SubmitResponse,
  V4StatusResponse,
  V4ResultResponse,
  V4ResultParams,
  V4BrowseResponse,
  V4BrowseParams,
  V4ValidateRequest,
  V4ValidateResponse,
  V4MaterialSystemSummary,
  V4MaterialSystemsParams,
  V4FigureResult,
  V4TableResult,
  V4PropertyResponse,
  SourceType as V4SourceType,
  JobStatus,
  Confidence as V4Confidence,
} from "./v4-extraction/types"
