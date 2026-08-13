import { describe, it, expect, vi, beforeEach } from "vitest"
import { GET } from "./route"
import { NextRequest } from "next/server"

// ---------------------------------------------------------------------------
// Mock global.fetch so we control upstream responses
// ---------------------------------------------------------------------------
const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function makeRequest(url: string): NextRequest {
  return new NextRequest(new URL(url, "http://localhost:3000"))
}

const FALLBACK_URL = "/ontology-viewer/data/nvl_ontology_data.json"

beforeEach(() => {
  mockFetch.mockReset()
})

// ---------------------------------------------------------------------------
// AC4: Empty or missing corpus param → 400
// ---------------------------------------------------------------------------
describe("GET /api/proxy/ontology/data — validation", () => {
  it("returns 400 when corpus param is missing", async () => {
    const req = makeRequest("/api/proxy/ontology/data")
    const res = await GET(req)
    expect(res.status).toBe(400)
    const body = await res.json()
    expect(body).toHaveProperty("error")
  })

  it("returns 400 when corpus param is empty string", async () => {
    const req = makeRequest("/api/proxy/ontology/data?corpus=")
    const res = await GET(req)
    expect(res.status).toBe(400)
  })

  it("returns 400 when corpus contains path traversal", async () => {
    const req = makeRequest("/api/proxy/ontology/data?corpus=../etc/passwd")
    const res = await GET(req)
    expect(res.status).toBe(400)
  })

  it("returns 400 when corpus contains slash", async () => {
    const req = makeRequest("/api/proxy/ontology/data?corpus=foo/bar")
    const res = await GET(req)
    expect(res.status).toBe(400)
  })
})

// ---------------------------------------------------------------------------
// AC1 & AC2: Valid corpus returns NVL JSON with Cache-Control
// ---------------------------------------------------------------------------
describe("GET /api/proxy/ontology/data — upstream success", () => {
  const mockGraph = {
    nodes: [{ id: "n1", label: "Uranium" }],
    edges: [{ source: "n1", target: "n2", relation: "related_to" }],
  }

  beforeEach(() => {
    const mockBody = JSON.stringify(mockGraph)
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({
        "content-type": "application/json",
      }),
      text: async () => mockBody,
    })
  })

  it("returns 200 with upstream JSON body", async () => {
    const req = makeRequest("/api/proxy/ontology/data?corpus=nuclear")
    const res = await GET(req)
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body).toEqual(mockGraph)
  })

  it("sets Cache-Control header", async () => {
    const req = makeRequest("/api/proxy/ontology/data?corpus=nuclear")
    const res = await GET(req)
    expect(res.headers.get("Cache-Control")).toBe("public, max-age=60, s-maxage=300")
  })

  it("fetches correct upstream URL with API_SERVER_URL", async () => {
    process.env.API_SERVER_URL = "http://backend:8100"
    const req = makeRequest("/api/proxy/ontology/data?corpus=nuclear")
    await GET(req)
    expect(mockFetch).toHaveBeenCalledWith(
      "http://backend:8100/api/v1/ontology/corpora/nuclear/graph",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  // NFM-2786: default to the Docker-internal service DNS instead of
  // localhost:8100 (which silently misroutes to whichever process
  // happens to occupy host port 8000 — Honcho in the current stack).
  it("falls back to the Docker-internal service DNS when API_SERVER_URL is unset", async () => {
    delete process.env.API_SERVER_URL
    const req = makeRequest("/api/proxy/ontology/data?corpus=nuclear")
    await GET(req)
    expect(mockFetch).toHaveBeenCalledWith(
      "http://nucpot-prod-api:8000/api/v1/ontology/corpora/nuclear/graph",
      expect.anything(),
    )
  })
})

// ---------------------------------------------------------------------------
// AC3: Upstream 5xx → 503 with fallback_url
// ---------------------------------------------------------------------------
describe("GET /api/proxy/ontology/data — upstream 5xx", () => {
  it("returns 503 with fallback_url on upstream 500", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      text: async () => "server error",
    })

    const req = makeRequest("/api/proxy/ontology/data?corpus=nuclear")
    const res = await GET(req)
    expect(res.status).toBe(503)
    const body = await res.json()
    expect(body).toEqual({
      error: "upstream_error",
      fallback_url: FALLBACK_URL,
    })
  })

  it("returns 503 on upstream 502", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      text: async () => "",
    })

    const req = makeRequest("/api/proxy/ontology/data?corpus=nuclear")
    const res = await GET(req)
    expect(res.status).toBe(503)
  })
})

// ---------------------------------------------------------------------------
// Upstream 4xx → forward status
// ---------------------------------------------------------------------------
describe("GET /api/proxy/ontology/data — upstream 4xx", () => {
  it("returns 404 on upstream 404", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: "Not Found",
      text: async () => '{"detail":"corpus not found"}',
    })

    const req = makeRequest("/api/proxy/ontology/data?corpus=nonexistent")
    const res = await GET(req)
    expect(res.status).toBe(404)
    const body = await res.json()
    expect(body).toHaveProperty("detail", "corpus not found")
  })

  it("returns 422 on upstream 422", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 422,
      statusText: "Unprocessable Entity",
      text: async () => '{"detail":"invalid corpus id"}',
    })

    // Use a valid-slug corpus so our regex passes but upstream returns 422
    const req = makeRequest("/api/proxy/ontology/data?corpus=validslug")
    const res = await GET(req)
    expect(res.status).toBe(422)
  })
})

// ---------------------------------------------------------------------------
// Network error / timeout → 503 with fallback_url
// ---------------------------------------------------------------------------
describe("GET /api/proxy/ontology/data — network error", () => {
  it("returns 503 on fetch timeout", async () => {
    mockFetch.mockRejectedValueOnce(new DOMException("The operation was aborted", "AbortError"))

    const req = makeRequest("/api/proxy/ontology/data?corpus=nuclear")
    const res = await GET(req)
    expect(res.status).toBe(503)
    const body = await res.json()
    expect(body).toEqual({
      error: "upstream_error",
      fallback_url: FALLBACK_URL,
    })
  })

  it("returns 503 on network failure", async () => {
    mockFetch.mockRejectedValueOnce(new TypeError("fetch failed"))

    const req = makeRequest("/api/proxy/ontology/data?corpus=nuclear")
    const res = await GET(req)
    expect(res.status).toBe(503)
    const body = await res.json()
    expect(body).toEqual({
      error: "upstream_error",
      fallback_url: FALLBACK_URL,
    })
  })
})
