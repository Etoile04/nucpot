import { describe, it, expect, vi, beforeEach } from "vitest"
import { GET } from "./route"
import { NextRequest } from "next/server"

// ---------------------------------------------------------------------------
// Mock global.fetch so we control upstream responses
// ---------------------------------------------------------------------------
const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

const VALID_UUID = "f999bb78-fe4a-45a7-9436-e08686c17c6b"

function makeRequest(idParam: string): NextRequest {
  return new NextRequest(
    new URL(`/api/potentials/${idParam}`, "http://localhost:3000"),
    {},
  )
}

beforeEach(() => {
  mockFetch.mockReset()
  // Default upstream URL; tests that exercise a non-default base override
  // via the API_SERVER_URL env via vi.stubEnv.
  delete process.env.API_SERVER_URL
})

// ---------------------------------------------------------------------------
// AC1 — empty / malformed id is rejected with 400 (no upstream call)
// ---------------------------------------------------------------------------
describe("GET /api/potentials/[id] — input validation", () => {
  it("returns 400 when id is missing from path", async () => {
    // The Next.js router always supplies a non-empty id here; the BFF
    // defends against that anyway in case someone wires it up wrong.
    const req = new NextRequest(
      new URL("http://localhost:3000/api/potentials/"),
      {},
    )
    const res = await GET(req, { params: Promise.resolve({ id: "" }) })
    expect(res.status).toBe(400)
    const body = await res.json()
    expect(body.error).toMatch(/required/)
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it("returns 400 when id is not a UUID (e.g. bare filename)", async () => {
    // This is the exact F2 leak vector: the previous BFF happily
    // queried Supabase with "Fe_Mendelev_2007v2.eam.fs" because it
    // was a free-text id filter. The new BFF rejects at the door.
    const res = await GET(makeRequest("Fe_Mendelev_2007v2.eam.fs"), {
      params: Promise.resolve({ id: "Fe_Mendelev_2007v2.eam.fs" }),
    })
    expect(res.status).toBe(400)
    const body = await res.json()
    expect(body.error).toMatch(/UUID/)
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it("returns 400 for path-traversal id", async () => {
    const res = await GET(makeRequest("..%2Fetc%2Fpasswd"), {
      params: Promise.resolve({ id: "../etc/passwd" }),
    })
    expect(res.status).toBe(400)
    expect(mockFetch).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// AC2 — success: upstream ApiResponse envelope is unwrapped, ``data`` returned
// ---------------------------------------------------------------------------
describe("GET /api/potentials/[id] — upstream success", () => {
  it("unwraps the ApiResponse envelope and returns the data payload", async () => {
    const potential = {
      id: VALID_UUID,
      name: "EAM_Fe_Mendelev_2007v2",
      type: "eam",
      elements: ["Fe"],
      file_url: "/api/v1/potentials/" + VALID_UUID + "/file",
    }
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      text: async () => JSON.stringify({ success: true, data: potential }),
    })
    const res = await GET(makeRequest(VALID_UUID), {
      params: Promise.resolve({ id: VALID_UUID }),
    })
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body).toEqual(potential)
    // Critical: the envelope must NOT leak to the FE.
    expect(body).not.toHaveProperty("success")
    expect(body).not.toHaveProperty("data")
  })

  it("targets FastAPI /api/v1/potentials/{id} (NOT cloud Supabase)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      text: async () => JSON.stringify({ success: true, data: { id: VALID_UUID } }),
    })
    await GET(makeRequest(VALID_UUID), {
      params: Promise.resolve({ id: VALID_UUID }),
    })
    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [calledUrl] = mockFetch.mock.calls[0] as [string]
    expect(calledUrl).toMatch(new RegExp(`/api/v1/potentials/${VALID_UUID}$`))
    // Must NOT carry any Supabase Storage host.
    expect(calledUrl).not.toMatch(/supabase\.co/)
    expect(calledUrl).not.toMatch(/postgrest/)
    expect(calledUrl).not.toMatch(/rest\/v1/)
  })

  it("honors API_SERVER_URL override for local dev", async () => {
    process.env.API_SERVER_URL = "http://localhost:8001"
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      text: async () => JSON.stringify({ success: true, data: { id: VALID_UUID } }),
    })
    await GET(makeRequest(VALID_UUID), {
      params: Promise.resolve({ id: VALID_UUID }),
    })
    const [calledUrl] = mockFetch.mock.calls[0] as [string]
    expect(calledUrl.startsWith("http://localhost:8001/api/v1/potentials/")).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// AC3 — failure paths forward the upstream status verbatim
// ---------------------------------------------------------------------------
describe("GET /api/potentials/[id] — upstream failure", () => {
  it("forwards 404 from FastAPI as 404 with upstream body", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      headers: new Headers({ "content-type": "application/json" }),
      text: async () =>
        JSON.stringify({ success: false, error: "Potential not found" }),
    })
    const res = await GET(makeRequest(VALID_UUID), {
      params: Promise.resolve({ id: VALID_UUID }),
    })
    expect(res.status).toBe(404)
    const body = await res.json()
    expect(body.error).toBe("Potential not found")
  })

  it("returns 502 when the upstream envelope is missing the data payload", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      text: async () => JSON.stringify({ success: false, error: "boom" }),
    })
    const res = await GET(makeRequest(VALID_UUID), {
      params: Promise.resolve({ id: VALID_UUID }),
    })
    expect(res.status).toBe(502)
    const body = await res.json()
    expect(body.error).toBe("upstream_error")
  })

  it("returns 502 when fetch itself throws (network/timeout)", async () => {
    mockFetch.mockRejectedValueOnce(new Error("ECONNREFUSED"))
    const res = await GET(makeRequest(VALID_UUID), {
      params: Promise.resolve({ id: VALID_UUID }),
    })
    expect(res.status).toBe(502)
    const body = await res.json()
    expect(body.error).toBe("upstream_unavailable")
    expect(body.detail).toMatch(/ECONNREFUSED/)
  })
})
