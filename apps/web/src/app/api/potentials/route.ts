import { NextResponse } from "next/server"

/**
 * Potentials list BFF route (NFM-4311 / BUG-30).
 *
 * Previously this route queried the remote cloud Supabase PostgREST
 * directly (`select("*")` + exact count). Origin-side measurements
 * (2026-09-05) showed that hop alone costs 0.39–1.58s — the dominant
 * share of the 1.2–1.83s production samples — while the local FastAPI
 * serving the same 65-row corpus from the co-located PostgreSQL answers
 * in ~5ms. The route now proxies the internal FastAPI
 * (`API_SERVER_URL`, same Docker network) and keeps the legacy HTTP
 * contract `{potentials, total, page, limit, totalPages}` (camelCase
 * totalPages) so no frontend change is required to benefit.
 *
 * The FastAPI endpoint caps `per_page` at 100; callers asking for a
 * larger page (the admin verification console fetches `limit=200`) get
 * backend pages stitched together here.
 */

/** FastAPI caps per_page at 100 (Query(le=100)). */
const BACKEND_PAGE_CAP = 100

/** Legacy effective cap (PostgREST max-rows): bounds backend fan-out. */
const LIMIT_MAX = 1000

/** Fail fast so the frontend auto-retry has a real chance to recover. */
const BACKEND_TIMEOUT_MS = 8_000

function backendBase(): string {
  // Docker prod/staging sets API_SERVER_URL to the compose service.
  // Local dev laptops run the API on host port 8001 (see next.config.ts).
  return process.env.API_SERVER_URL ?? "http://localhost:8001"
}

interface BackendListPayload {
  potentials: unknown[]
  total: number
  page: number
  limit: number
  total_pages: number
}

interface FetchPageResult {
  payload: BackendListPayload | null
  error: string | null
}

async function fetchBackendPage(sp: URLSearchParams): Promise<FetchPageResult> {
  const url = `${backendBase()}/api/v1/potentials?${sp.toString()}`
  const timeoutSignal =
    typeof AbortSignal !== "undefined" && "timeout" in AbortSignal
      ? AbortSignal.timeout(BACKEND_TIMEOUT_MS)
      : undefined
  try {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      signal: timeoutSignal,
    })
    if (!response.ok) {
      return {
        payload: null,
        error: `后端返回 ${response.status} ${response.statusText || ""}`.trim(),
      }
    }
    const envelope = (await response.json()) as {
      success?: boolean
      data?: BackendListPayload
      error?: string
    }
    if (!envelope.success || !envelope.data) {
      return { payload: null, error: envelope.error ?? "后端响应格式异常" }
    }
    return { payload: envelope.data, error: null }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    return { payload: null, error: `后端不可达: ${message}` }
  }
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)

  const page = Math.max(1, parseInt(searchParams.get("page") || "1") || 1)
  const limit = Math.min(LIMIT_MAX, Math.max(1, parseInt(searchParams.get("limit") || "20") || 20))

  // Filter passthrough (contract parity with the legacy Supabase route,
  // now implemented natively by the FastAPI service layer).
  const FORWARD_PARAMS = [
    "type",
    "elements",
    "q",
    "sort",
    "irradiation",
    "hasDefect",
    "hasLiquid",
    "validationLevel",
    "tempMin",
    "tempMax",
  ] as const

  const base = new URLSearchParams()
  for (const key of FORWARD_PARAMS) {
    const value = searchParams.get(key)
    if (value !== null && value !== "") base.set(key, value)
  }

  // The caller's window starts at legacy row offset (page-1)*limit. For
  // limit <= the backend cap the backend page grid is aligned with that
  // window; beyond it, start at the backend page holding the first wanted
  // row and drop the leading rows that belong to earlier windows.
  const offset = (page - 1) * limit
  const stitch = limit > BACKEND_PAGE_CAP
  const firstPage = stitch ? Math.floor(offset / BACKEND_PAGE_CAP) + 1 : page
  const skip = stitch ? offset % BACKEND_PAGE_CAP : 0

  const firstSp = new URLSearchParams(base)
  firstSp.set("page", String(firstPage))
  firstSp.set("per_page", String(stitch ? BACKEND_PAGE_CAP : limit))
  const first = await fetchBackendPage(firstSp)
  if (first.error || !first.payload) {
    return NextResponse.json({ error: first.error ?? "后端错误" }, { status: 502 })
  }

  const total = first.payload.total
  let potentials = first.payload.potentials.slice(skip)

  // Stitch additional backend pages only while the caller's window is not
  // full and the corpus still has rows to give. A short page means the
  // corpus is exhausted.
  if (stitch) {
    let cursorPage = firstPage
    while (potentials.length < limit && offset + potentials.length < total) {
      cursorPage += 1
      const nextSp = new URLSearchParams(base)
      nextSp.set("page", String(cursorPage))
      nextSp.set("per_page", String(BACKEND_PAGE_CAP))
      const next = await fetchBackendPage(nextSp)
      if (next.error || !next.payload) break // serve what we have
      potentials = potentials.concat(next.payload.potentials)
      if (next.payload.potentials.length < BACKEND_PAGE_CAP) break
    }
    potentials = potentials.slice(0, limit)
  }

  return NextResponse.json({
    potentials,
    total,
    page,
    limit,
    totalPages: Math.max(1, Math.ceil((total || 0) / limit)),
  })
}
