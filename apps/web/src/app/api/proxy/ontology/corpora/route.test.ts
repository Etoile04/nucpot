import { describe, it, expect, vi, beforeEach } from "vitest"
import { GET } from "./route"

// ---------------------------------------------------------------------------
// Mock global.fetch so we control the upstream response
// ---------------------------------------------------------------------------
const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

const STATIC_ENTRY = {
  id: "default",
  name: "OntoFuel Nuclear Materials (Default)",
  asset_url: "./data/nvl_ontology_data.json",
}

beforeEach(() => {
  mockFetch.mockReset()
})

// ---------------------------------------------------------------------------
// AC-2: upstream success — static + dynamic merge
// ---------------------------------------------------------------------------
describe("GET /api/proxy/ontology/corpora — upstream success", () => {
  it("merges static default corpus with backend corpora", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        corpora: [
          { corpus_id: "Smirnov2014", row_count: 2, last_updated: "2026-07-06T01:04:44Z" },
          { corpus_id: "10.1016-j.jnucmat.2023.154543", row_count: 3, last_updated: null },
        ],
      }),
    })

    const res = await GET()
    expect(res.status).toBe(200)
    const body = await res.json()

    expect(body.default_corpus).toBe("default")
    expect(body.corpora).toHaveLength(3)
    expect(body.corpora[0]).toMatchObject(STATIC_ENTRY)

    const dyn = body.corpora[1]
    expect(dyn.id).toBe("Smirnov2014")
    expect(dyn.asset_url).toBe("/api/proxy/ontology/data?corpus=Smirnov2014")
    // Slash-free DOI-ish ids are valid slugs (dot/dash allowed) and survive.
    expect(body.corpora[2].id).toBe("10.1016-j.jnucmat.2023.154543")
  })

  it("proxies to the Docker-internal API by default (NFM-2786)", async () => {
    delete process.env.API_SERVER_URL
    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ corpora: [] }) })

    await GET()
    expect(mockFetch).toHaveBeenCalledWith(
      "http://nucpot-prod-api:8000/api/v1/ontology/corpora",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it("honors API_SERVER_URL when set", async () => {
    process.env.API_SERVER_URL = "http://backend:8100"
    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ corpora: [] }) })

    await GET()
    expect(mockFetch).toHaveBeenCalledWith(
      "http://backend:8100/api/v1/ontology/corpora",
      expect.anything(),
    )
    delete process.env.API_SERVER_URL
  })

  it("drops corpus ids that are not safe slugs", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        corpora: [
          { corpus_id: "good-one", row_count: 1, last_updated: null },
          { corpus_id: "bad/slug", row_count: 1, last_updated: null },
          { corpus_id: "../etc/passwd", row_count: 1, last_updated: null },
        ],
      }),
    })

    const res = await GET()
    const body = await res.json()
    const dynamic = body.corpora.filter(
      (c: { source_digest: string }) => c.source_digest === "dynamic",
    )
    expect(dynamic.map((c: { id: string }) => c.id)).toEqual(["good-one"])
  })
})

// ---------------------------------------------------------------------------
// AC-2: fail-soft — upstream non-200 / network error / malformed body
// ---------------------------------------------------------------------------
describe("GET /api/proxy/ontology/corpora — fail-soft degradation", () => {
  it("returns static-only index when upstream returns 500", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 500 })
    const res = await GET()
    expect(res.status).toBe(200) // never 5xx
    const body = await res.json()
    expect(body.corpora).toHaveLength(1)
    expect(body.corpora[0]).toMatchObject(STATIC_ENTRY)
  })

  it("returns static-only index on network error (fetch rejects)", async () => {
    mockFetch.mockRejectedValueOnce(new TypeError("fetch failed"))
    const res = await GET()
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.corpora).toHaveLength(1)
  })

  it("returns static-only index when upstream body has no corpora field", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({}) })
    const res = await GET()
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.corpora).toHaveLength(1)
  })
})

// ---------------------------------------------------------------------------
// NFM-3303 regression: the ghost `nuclear` corpus must never reappear
// ---------------------------------------------------------------------------
describe("GET /api/proxy/ontology/corpora — ghost corpus regression", () => {
  it("never emits an entry for a corpus the backend did not report", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        corpora: [{ corpus_id: "Smirnov2014", row_count: 2, last_updated: null }],
      }),
    })
    const res = await GET()
    const body = await res.json()
    const ids = body.corpora.map((c: { id: string }) => c.id)
    expect(ids).not.toContain("nuclear")
  })
})
