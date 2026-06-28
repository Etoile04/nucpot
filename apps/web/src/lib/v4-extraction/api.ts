/**
 * V4 Extraction API client.
 *
 * Provides typed functions for all 6 backend endpoints.
 * Follows the same patterns as lib/md-verification-api.ts.
 */

import type {
  ApiResponse,
  V4BrowseParams,
  V4BrowseResponse,
  V4ExtractionSubmitRequest,
  V4MaterialSystemSummary,
  V4MaterialSystemsParams,
  V4ResultParams,
  V4ResultResponse,
  V4StatusResponse,
  V4SubmitResponse,
  V4ValidateRequest,
  V4ValidateResponse,
} from "./types"

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"

// ─── Helpers ───────────────────────────────────────────────────

function getAuthToken(): string | null {
  if (typeof window === "undefined") return null
  return localStorage.getItem("auth_token")
}

function getHeaders(): HeadersInit {
  const headers: HeadersInit = {
    "Content-Type": "application/json",
  }
  const token = getAuthToken()
  if (token) {
    headers["Authorization"] = `Bearer ${token}`
  }
  return headers
}

async function handleResponse<T>(
  response: Response,
): Promise<{ data: T; meta?: Record<string, unknown> }> {
  if (!response.ok) {
    const errorBody = await response.json().catch(() => null)
    const message =
      errorBody?.detail ??
      errorBody?.error ??
      `请求失败 (${response.status})`
    throw new Error(message)
  }

  const result: ApiResponse<T> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error ?? "请求失败")
  }

  return { data: result.data, meta: result.meta }
}

function buildQueryString(params: Record<string, unknown>): string {
  const searchParams = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      searchParams.append(key, String(value))
    }
  }
  const qs = searchParams.toString()
  return qs ? `?${qs}` : ""
}

// ─── API Functions ──────────────────────────────────────────────

/**
 * POST /api/v4/extraction/submit
 * Submit a new extraction job.
 */
export async function submitExtractionJob(
  payload: V4ExtractionSubmitRequest,
): Promise<V4SubmitResponse> {
  const response = await fetch(`${API_BASE}/api/v4/extraction/submit`, {
    method: "POST",
    headers: getHeaders(),
    body: JSON.stringify(payload),
  })
  const { data } = await handleResponse<V4SubmitResponse>(response)
  return data
}

/**
 * GET /api/v4/extraction/{job_id}/status
 * Poll extraction job progress.
 */
export async function getExtractionStatus(
  jobId: string,
): Promise<V4StatusResponse> {
  const response = await fetch(
    `${API_BASE}/api/v4/extraction/${jobId}/status`,
    {
      method: "GET",
      headers: getHeaders(),
    },
  )
  const { data } = await handleResponse<V4StatusResponse>(response)
  return data
}

/**
 * GET /api/v4/extraction/{job_id}/result
 * Retrieve extraction results with optional filters.
 */
export async function getExtractionResults(
  jobId: string,
  params: V4ResultParams = {},
): Promise<{ data: V4ResultResponse; meta?: Record<string, unknown> }> {
  const qs = buildQueryString(params as Record<string, unknown>)
  const response = await fetch(
    `${API_BASE}/api/v4/extraction/${jobId}/result${qs}`,
    {
      method: "GET",
      headers: getHeaders(),
    },
  )
  return handleResponse<V4ResultResponse>(response)
}

/**
 * GET /api/v4/properties/{material_system}
 * Browse extracted properties for a material system.
 */
export async function browseProperties(
  materialSystem: string,
  params: V4BrowseParams = {},
): Promise<{ data: V4BrowseResponse; meta?: Record<string, unknown> }> {
  const qs = buildQueryString(params as Record<string, unknown>)
  const response = await fetch(
    `${API_BASE}/api/v4/properties/${encodeURIComponent(materialSystem)}${qs}`,
    {
      method: "GET",
      headers: getHeaders(),
    },
  )
  return handleResponse<V4BrowseResponse>(response)
}

/**
 * POST /api/v4/extraction/{job_id}/validate
 * Trigger validation workflow for extraction results.
 */
export async function validateExtractionResults(
  jobId: string,
  payload?: V4ValidateRequest,
): Promise<V4ValidateResponse> {
  const response = await fetch(
    `${API_BASE}/api/v4/extraction/${jobId}/validate`,
    {
      method: "POST",
      headers: getHeaders(),
      body: JSON.stringify(payload ?? {}),
    },
  )
  const { data } = await handleResponse<V4ValidateResponse>(response)
  return data
}

/**
 * GET /api/v4/material-systems
 * List all material systems with optional filters.
 */
export async function getMaterialSystems(
  params: V4MaterialSystemsParams = {},
): Promise<V4MaterialSystemSummary[]> {
  const qs = buildQueryString(params as Record<string, unknown>)
  const response = await fetch(
    `${API_BASE}/api/v4/material-systems${qs}`,
    {
      method: "GET",
      headers: getHeaders(),
    },
  )
  const { data } = await handleResponse<V4MaterialSystemSummary[]>(response)
  return data
}
