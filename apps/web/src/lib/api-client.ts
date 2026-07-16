/**
 * Shared API client for backend communication.
 *
 * Authentication uses HttpOnly cookies set by the server.
 * All requests include credentials:"include" to send cookies automatically.
 * No localStorage token management needed (XSS-safe).
 */

export interface ApiError {
  readonly detail?: string
  readonly message?: string
}

function buildHeaders(custom?: Record<string, string>): HeadersInit {
  return {
    "Content-Type": "application/json",
    ...custom,
  }
}

/**
 * Generic request wrapper.
 * Throws on non-OK responses with a descriptive error message.
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
