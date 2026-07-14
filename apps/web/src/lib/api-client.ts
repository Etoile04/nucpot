/**
 * Shared API client for backend communication.
 *
 * Reads JWT from localStorage and attaches Authorization header.
 * All blog admin API calls go through this client.
 *
 * Uses relative paths — next.config.ts rewrite proxy handles backend routing.
 */

const TOKEN_KEY = "blog_admin_token"

export interface ApiError {
  readonly detail?: string
  readonly message?: string
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

function buildHeaders(custom?: Record<string, string>): HeadersInit {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...custom,
  }

  const token = getToken()
  if (token) {
    headers["Authorization"] = `Bearer ${token}`
  }

  return headers
}

/**
 * Generic request wrapper.
 * Throws on non-OK responses with a descriptive error message.
 */
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = path
  const response = await fetch(url, {
    ...options,
    headers: buildHeaders(
      options.headers as Record<string, string> | undefined,
    ),
  })

  if (response.status === 401) {
    clearToken()
    if (typeof window !== "undefined") {
      window.location.href = "/admin/login"
    }
    throw new Error("认证已过期，请重新登录")
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiError | null
    const message = body?.detail ?? body?.message ?? `请求失败 (${response.status})`
    throw new Error(message)
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

interface ApiResponse<T> {
  readonly success: boolean
  readonly data: T
}

export const authApi = {
  login: async (username: string, password: string): Promise<TokenResponse> => {
    const body = new URLSearchParams()
    body.append("username", username)
    body.append("password", password)

    const url = "/api/v1/auth/login"
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    })

    if (response.status === 401) {
      throw new Error("邮箱或密码错误")
    }

    if (!response.ok) {
      throw new Error("登录失败，请稍后重试")
    }

    return response.json() as Promise<TokenResponse>
  },

  getMe: (): Promise<UserProfile> =>
    request<ApiResponse<UserProfile>>("/api/v1/auth/me").then((r) => r.data),

  logout: (): void => {
    clearToken()
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
  // Fields populated from markdown file
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
  readonly source_type: string
  readonly status: string
  readonly message: string
}

export interface ExtractionStatusResponse {
  readonly job_id: string
  readonly source_reference: string
  readonly source_type: string
  readonly status: string
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
