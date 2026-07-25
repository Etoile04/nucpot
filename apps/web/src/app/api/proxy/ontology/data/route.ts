import { NextRequest, NextResponse } from "next/server"

const FALLBACK_URL = "/ontology-viewer/data/nvl_ontology_data.json"

function getApiServerUrl(): string {
  return process.env.API_SERVER_URL ?? "http://localhost:8100"
}

/** Corpus IDs must be a safe slug — matches backend CORPUS_ID_PATTERN. */
const CORPUS_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

const UPSTREAM_TIMEOUT_MS = 15_000

function badRequest(message: string): NextResponse {
  return NextResponse.json({ error: message }, { status: 400 })
}

function serviceUnavailable(): NextResponse {
  return NextResponse.json(
    { error: "upstream_error", fallback_url: FALLBACK_URL },
    { status: 503 },
  )
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const corpus = req.nextUrl.searchParams.get("corpus")

  // --- Validate corpus param ---
  if (!corpus) {
    return badRequest("corpus query parameter is required")
  }
  if (!CORPUS_ID_RE.test(corpus)) {
    return badRequest("corpus query parameter contains invalid characters")
  }

  // --- Fetch from upstream ---
  const upstreamUrl = `${getApiServerUrl()}/api/v1/ontology/corpora/${corpus}/graph`
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS)

  try {
    const upstream = await fetch(upstreamUrl, { signal: controller.signal })
    clearTimeout(timeout)

    const body = await upstream.text()

    // Upstream 5xx → 503 with fallback
    if (upstream.status >= 500) {
      return serviceUnavailable()
    }

    // Upstream 4xx → forward status and body
    if (!upstream.ok) {
      return NextResponse.json(JSON.parse(body), {
        status: upstream.status,
        headers: { "Content-Type": "application/json" },
      })
    }

    // Success → return JSON with Cache-Control
    return new NextResponse(body, {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "public, max-age=60, s-maxage=300",
      },
    })
  } catch (error: unknown) {
    clearTimeout(timeout)
    return serviceUnavailable()
  }
}
