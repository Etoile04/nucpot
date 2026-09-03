import { describe, it, expect, vi, beforeEach } from "vitest"
import { POST, GET } from "./route"
import { NextRequest } from "next/server"

// ---------------------------------------------------------------------------
// Mock the Supabase clients so no network / DB access happens in tests.
// ---------------------------------------------------------------------------
const mockInsert = vi.fn()
vi.mock("@/lib/supabase", () => ({
  supabase: {
    from: vi.fn(() => ({ insert: mockInsert })),
  },
  supabaseAdmin: null,
}))

function makeRequest(body: unknown, raw?: string): NextRequest {
  const text = raw ?? JSON.stringify(body)
  return new NextRequest("http://localhost:3000/api/analytics/events", {
    method: "POST",
    body: text,
  })
}

const NOW = Date.now()

beforeEach(() => {
  mockInsert.mockReset()
  mockInsert.mockResolvedValue({ error: null })
  vi.spyOn(Date, "now").mockReturnValue(NOW)
})

describe("POST /api/analytics/events — validation", () => {
  it("returns 400 on invalid JSON", async () => {
    const res = await POST(makeRequest(null, "{not json"))
    expect(res.status).toBe(400)
  })

  it("returns 400 on unknown event name", async () => {
    const res = await POST(makeRequest({ event: "evil.event", ts: NOW }))
    expect(res.status).toBe(400)
    expect(mockInsert).not.toHaveBeenCalled()
  })

  it("returns 400 when ts is missing or non-numeric", async () => {
    const res = await POST(
      makeRequest({ event: "data_loss_notice.viewed", ts: "yesterday" }),
    )
    expect(res.status).toBe(400)
  })

  it("returns 400 when ts is too far from server time", async () => {
    const res = await POST(
      makeRequest({ event: "data_loss_notice.viewed", ts: NOW - 25 * 60 * 60 * 1000 }),
    )
    expect(res.status).toBe(400)
  })

  it("returns 400 when payload is an array", async () => {
    const res = await POST(
      makeRequest({ event: "data_loss_notice.viewed", ts: NOW, payload: ["a"] }),
    )
    expect(res.status).toBe(400)
  })

  it("returns 400 when payload contains an oversized string", async () => {
    const res = await POST(
      makeRequest({
        event: "data_loss_notice.viewed",
        ts: NOW,
        payload: { surface: "x".repeat(300) },
      }),
    )
    expect(res.status).toBe(400)
  })

  it("returns 400 when body exceeds the size cap", async () => {
    const res = await POST(makeRequest(null, "x".repeat(5 * 1024)))
    expect(res.status).toBe(400)
  })
})

describe("POST /api/analytics/events — happy path", () => {
  it("inserts each contract event with the payload verbatim and returns 204", async () => {
    const events = [
      { event: "data_loss_notice.viewed", payload: { measurementId: "m1", datasetId: "d1" } },
      {
        event: "data_loss_notice.dismissed",
        payload: { measurementId: "m1", dwellMs: 4200 },
      },
      {
        event: "data_loss_notice.learn_more_clicked",
        payload: { measurementId: "m1", surface: "materials" },
      },
    ] as const

    for (const e of events) {
      mockInsert.mockClear()
      const res = await POST(makeRequest({ ...e, ts: NOW }))
      expect(res.status).toBe(204)
      expect(mockInsert).toHaveBeenCalledTimes(1)
      expect(mockInsert).toHaveBeenCalledWith({
        event: e.event,
        payload: e.payload,
        client_ts: NOW,
      })
    }
  })

  it("defaults a missing payload to an empty object", async () => {
    const res = await POST(makeRequest({ event: "data_loss_notice.viewed", ts: NOW }))
    expect(res.status).toBe(204)
    expect(mockInsert).toHaveBeenCalledWith({
      event: "data_loss_notice.viewed",
      payload: {},
      client_ts: NOW,
    })
  })
})

describe("POST /api/analytics/events — ingestion failure", () => {
  it("returns 500 and does not throw when the insert errors", async () => {
    mockInsert.mockResolvedValue({ error: { message: "relation missing" } })
    const res = await POST(makeRequest({ event: "data_loss_notice.viewed", ts: NOW }))
    expect(res.status).toBe(500)
  })
})

describe("GET /api/analytics/events", () => {
  it("returns 405", async () => {
    const res = await GET()
    expect(res.status).toBe(405)
  })
})
