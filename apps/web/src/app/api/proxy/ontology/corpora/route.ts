import { NextResponse } from "next/server"

/**
 * NFM-3303 — dynamic corpus index aggregator.
 *
 * The vendored viewer fetches a build-time static corpus index
 * (`/ontology-viewer/data/corpus/index.json`) whose "dynamic" entry
 * advertised a `nuclear` corpus that never existed in the DB → the graph
 * request 404'd and the viewer surfaced `HTTP 404: Unknown error`.
 *
 * This route merges:
 *   1. the static default (build-baked) corpus, and
 *   2. corpora the backend reports as actually queryable
 *      (`GET /api/v1/ontology/corpora` — derived from staging rows),
 * emitting the exact index shape the viewer consumes:
 *   { corpora: [{id, name, description, asset_url, ...}], default_corpus }
 *
 * Fail-soft: if the backend is unreachable/slow/erroring, return the static
 * default corpus only — the viewer then still renders the build-time graph.
 * This route never 5xx's.
 */

// Mirrors the viewer's data URL for the static corpus (Phase 0 contract).
// Relative form — the viewer resolves asset_url against its page origin
// (/ontology-viewer/index.html), matching the build-time static index.
const STATIC_ASSET_URL = "./data/nvl_ontology_data.json"
const STATIC_CORPUS_ID = "default"

// NFM-2786: default to the Docker-internal service DNS to avoid the
// silent localhost:8100 → host-port-8000 (Honcho) misroute.  Local
// dev (API outside Docker) must set API_SERVER_URL explicitly.
function getApiServerUrl(): string {
  return process.env.API_SERVER_URL ?? "http://nucpot-prod-api:8000"
}

const UPSTREAM_TIMEOUT_MS = 15_000

interface UpstreamCorpus {
  corpus_id: string
  row_count: number
  last_updated: string | null
}

/** Corpus IDs must be a safe slug — matches backend CORPUS_ID_PATTERN. */
const CORPUS_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

interface IndexCorpus {
  id: string
  name: string
  description: string
  asset_url: string
  source_digest: string
  schema_version: string
}

function staticIndex(): { corpora: IndexCorpus[]; default_corpus: string } {
  return {
    corpora: [
      {
        id: STATIC_CORPUS_ID,
        name: "OntoFuel Nuclear Materials (Default)",
        description: "构建时内置的静态本体数据",
        asset_url: STATIC_ASSET_URL,
        source_digest: "static",
        schema_version: "1.0",
      },
    ],
    default_corpus: STATIC_CORPUS_ID,
  }
}

export async function GET(): Promise<NextResponse> {
  const fallback = staticIndex()

  const upstreamUrl = `${getApiServerUrl()}/api/v1/ontology/corpora`
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS)

  try {
    const upstream = await fetch(upstreamUrl, { signal: controller.signal })
    clearTimeout(timeout)

    if (!upstream.ok) {
      // Backend answered but not 200 (e.g. 429/5xx) — degrade to static-only.
      return NextResponse.json(fallback, {
        headers: { "Cache-Control": "public, max-age=30, s-maxage=60" },
      })
    }

    const body = (await upstream.json()) as { corpora?: UpstreamCorpus[] }
    const dynamic: IndexCorpus[] = (body.corpora ?? [])
      .filter((c) => CORPUS_ID_RE.test(c.corpus_id))
      .map((c) => ({
        id: c.corpus_id,
        name: c.corpus_id,
        description: `后端动态语料库（${c.row_count} 条数据）`,
        asset_url: `/api/proxy/ontology/data?corpus=${encodeURIComponent(c.corpus_id)}`,
        source_digest: "dynamic",
        schema_version: "1.0",
      }))

    return NextResponse.json(
      { corpora: [...fallback.corpora, ...dynamic], default_corpus: STATIC_CORPUS_ID },
      { headers: { "Cache-Control": "public, max-age=30, s-maxage=60" } },
    )
  } catch {
    clearTimeout(timeout)
    // Network error / timeout — degrade to static-only, never 5xx.
    return NextResponse.json(fallback, {
      headers: { "Cache-Control": "public, max-age=30, s-maxage=60" },
    })
  }
}
