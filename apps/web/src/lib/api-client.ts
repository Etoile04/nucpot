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

/**
 * HTTP error thrown by {@link request} when the response status is not OK.
 *
 * Carries the numeric status code so callers can branch on it (e.g.
 * distinguishing 404 not-found from generic 5xx) instead of fragile
 * string matching on the localized message.
 */
export class ApiHttpError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = "ApiHttpError"
    this.status = status
  }
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
    throw new ApiHttpError(401, "认证已过期，请重新登录")
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiError | null
    const message = body?.detail ?? body?.message ?? `请求失败 (${response.status})`
    throw new ApiHttpError(response.status, message)
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
