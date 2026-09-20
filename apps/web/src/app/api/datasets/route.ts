import { NextResponse } from "next/server"

/**
 * BFF route for /api/datasets — list.
 *
 * NFM-4991 (IA-REFACTOR P2 /datasets block). Proxies the FastAPI
 * ``GET /api/v1/datasets`` endpoint and unwraps the standard
 * {success, data, error} envelope.  Filter params are forwarded as-is
 * so the frontend doesn't need to translate between camelCase and
 * snake_case twice.
 *
 * The default anonymous list response does NOT include material_name /
 * source_title (those need ``expand=material,source``) — keeps the
 * common case cheap and avoids dragging extra JOINs into the homepage
 * stat tiles or admin consoles that might piggy-back on this endpoint.
 */

const BACKEND_TIMEOUT_MS = 8_000

function backendBase(): string {
  return process.env.API_SERVER_URL ?? "http://localhost:8001"
}

interface BackendListEnvelope {
  success?: boolean
  data?: {
    items: unknown[]
    total: number
    page: number
    limit: number
    pages: number
    truncated: boolean
  }
  error?: string
}

export async function GET(request: Request): Promise<NextResponse> {
  const url = new URL(request.url)
  const sp = url.searchParams

  // Forward the canonical backend query keys. The frontend never sends
  // page_size / size — both are normalized to per_page on the client.
  const backendParams = new URLSearchParams()
  const page = sp.get("page")
  const perPage = sp.get("per_page")
  const materialId = sp.get("material_id")
  const sourceId = sp.get("source_id")
  const isVerified = sp.get("is_verified")
  const expand = sp.get("expand")
  if (page) backendParams.set("page", page)
  if (perPage) backendParams.set("per_page", perPage)
  if (materialId) backendParams.set("material_id", materialId)
  if (sourceId) backendParams.set("source_id", sourceId)
  if (isVerified !== null) backendParams.set("is_verified", isVerified)
  if (expand) backendParams.set("expand", expand)

  const target = `${backendBase()}/api/v1/datasets?${backendParams.toString()}`
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), BACKEND_TIMEOUT_MS)
  try {
    const response = await fetch(target, {
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      cache: "no-store",
    })
    if (!response.ok) {
      return NextResponse.json(
        {
          success: false,
          error: `后端返回 ${response.status} ${response.statusText || ""}`.trim(),
        },
        { status: response.status },
      )
    }
    const envelope = (await response.json()) as BackendListEnvelope
    if (!envelope.success || !envelope.data) {
      return NextResponse.json(
        {
          success: false,
          error: envelope.error ?? "后端响应格式异常",
        },
        { status: 502 },
      )
    }
    return NextResponse.json({ success: true, data: envelope.data })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json(
      {
        success: false,
        error: `后端不可达: ${message}`,
      },
      { status: 502 },
    )
  } finally {
    clearTimeout(timeout)
  }
}