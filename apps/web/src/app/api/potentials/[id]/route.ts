import { NextRequest, NextResponse } from "next/server"

// F2 (NFM-4309 / BUG-37 QA-FAILED): the previous implementation queried
// the *cloud* Supabase ``potentials`` table directly. Cloud Supabase
// still carries the unmigrated ``/app/uploads/Fe_Mendelev_2007v2.eam.fs``
// row (live 404 confirmed) — bypassing this BFF lets that dead link
// reach the FE compare/detail pages, which is the BUG-37 leak the
// canonical-proxy spec was added to prevent.
//
// Re-target the BFF at the FastAPI backend, which serves the canonical
// ``ApiResponse<PotentialDetail>`` envelope. The FE ``PotentialDetail``
// type (apps/web/src/lib/potentials-api.ts) matches the backend's
// snake_case schema, so we forward ``data`` directly without key
// reshaping. Anonymous read access (no auth header) — matches the
// backend route's lack of a ``Depends(get_current_active_user)``.

const UPSTREAM_TIMEOUT_MS = 15_000

// UUID v1-5 (dashes optional, hex 8-4-4-4-12). Matches what the backend
// accepts in the path; we reject anything else with a fast 400 so we
// don't waste an upstream round-trip on a typo.
const UUID_RE = /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i

function getApiServerUrl(): string {
  // Same convention as the other FastAPI-proxying BFFs (e.g. /api/stats
  // and /api/proxy/ontology/data): server-side API base, defaulting to
  // the Docker-internal DNS so prod picks up the API container
  // automatically and local dev overrides via env.
  return process.env.API_SERVER_URL ?? "http://nucpot-prod-api:8000"
}

function badRequest(message: string): NextResponse {
  return NextResponse.json({ error: message }, { status: 400 })
}

function serviceUnavailable(detail?: string): NextResponse {
  return NextResponse.json(
    { error: "upstream_unavailable", detail },
    { status: 502 },
  )
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params

  // Strip whitespace + trim, then validate — the path param is what
  // the FE built from a UUID, but a stray space from a copy-paste is a
  // common source of 404 noise that we should fail fast on.
  const candidate = id?.trim()
  if (!candidate) {
    return badRequest("id path parameter is required")
  }
  if (!UUID_RE.test(candidate)) {
    return badRequest(`id is not a valid UUID: ${candidate}`)
  }

  const upstreamUrl = `${getApiServerUrl()}/api/v1/potentials/${encodeURIComponent(candidate)}`
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS)

  try {
    const upstream = await fetch(upstreamUrl, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    })
    clearTimeout(timeout)

    const body = await upstream.text()

    // Forward 4xx (404 = potential not found or unpublished on backend)
    // and 5xx (502 with a clean error envelope) verbatim — no
    // transformation. The FE compare page reads ``r.ok`` and throws
    // on false, so the status code must propagate.
    if (!upstream.ok) {
      let parsed: unknown = body
      try {
        parsed = JSON.parse(body)
      } catch {
        // Body wasn't JSON — keep the raw string so the FE still sees
        // *something* meaningful in the error path.
      }
      return NextResponse.json(parsed, {
        status: upstream.status,
        headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json" },
      })
    }

    // Unwrap the FastAPI ``ApiResponse<T>`` envelope and forward the
    // ``data`` payload as the BFF response body. The FE
    // ``PotentialDetail`` type matches the backend snake_case schema
    // — no key reshaping required. Returning the envelope here would
    // break every consumer that does ``await res.json()`` expecting a
    // single potential record.
    let envelope: { success?: boolean; data?: unknown; error?: unknown }
    try {
      envelope = JSON.parse(body)
    } catch {
      return serviceUnavailable("upstream returned non-JSON body")
    }
    if (!envelope || envelope.success !== true || envelope.data === undefined) {
      // The backend returned a non-success envelope; surface as 502
      // rather than silently forwarding the (potentially empty) data
      // payload — the FE has no way to distinguish that from a real
      // potential record.
      return NextResponse.json(
        { error: "upstream_error", detail: envelope?.error ?? "missing data" },
        { status: 502 },
      )
    }
    return NextResponse.json(envelope.data, {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        // short edge cache: potential metadata is stable until an
        // admin update; ``s-maxage`` lets the CDN coalesce repeated
        // compare-page fetches for the same id.
        "Cache-Control": "public, max-age=30, s-maxage=120",
      },
    })
  } catch (error: unknown) {
    clearTimeout(timeout)
    const msg = error instanceof Error ? error.message : String(error)
    return serviceUnavailable(msg)
  }
}
