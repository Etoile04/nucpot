import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import {
  trackDataLossNotice,
  type DataLossNoticeEventPayload,
} from "../analytics"

// ---------------------------------------------------------------------------
// NFM-4181: the stub is gone — trackDataLossNotice must dispatch to the
// internal events pipeline (/api/analytics/events) while keeping the
// window.__nfmdAnalyticsQueue mirror for test assertions.
// ---------------------------------------------------------------------------

const mockSendBeacon = vi.fn(() => true)

beforeEach(() => {
  mockSendBeacon.mockClear()
  Object.defineProperty(navigator, "sendBeacon", {
    configurable: true,
    writable: true,
    value: mockSendBeacon,
  })
  delete window.__nfmdAnalyticsQueue
})

afterEach(() => {
  delete window.__nfmdAnalyticsQueue
})

const PAYLOAD: DataLossNoticeEventPayload = {
  measurementId: "synthetic-m1",
  datasetId: "synthetic-d1",
  surface: "materials",
}

describe("trackDataLossNotice — pipeline dispatch", () => {
  it("sendBeacons each contract event with unchanged name and payload", async () => {
    const before = Date.now()
    trackDataLossNotice("data_loss_notice.viewed", PAYLOAD)

    expect(mockSendBeacon).toHaveBeenCalledTimes(1)
    const [url, blob] = mockSendBeacon.mock.calls[0] as unknown as [string, Blob]
    expect(url).toBe("/api/analytics/events")

    const text = await blob.text()
    const parsed = JSON.parse(text) as {
      event: string
      payload: DataLossNoticeEventPayload
      ts: number
    }
    expect(parsed.event).toBe("data_loss_notice.viewed")
    expect(parsed.payload).toEqual(PAYLOAD)
    expect(parsed.ts).toBeGreaterThanOrEqual(before)
  })

  it("still mirrors onto window.__nfmdAnalyticsQueue (test escape hatch)", () => {
    trackDataLossNotice("data_loss_notice.dismissed", {
      ...PAYLOAD,
      dwellMs: 2500,
    })
    const entry = window.__nfmdAnalyticsQueue?.[0]
    expect(window.__nfmdAnalyticsQueue).toHaveLength(1)
    expect(entry?.event).toBe("data_loss_notice.dismissed")
    expect(entry?.payload.dwellMs).toBe(2500)
    expect(typeof entry?.ts).toBe("number")
  })

  it("does not throw when sendBeacon throws", () => {
    mockSendBeacon.mockImplementation(() => {
      throw new Error("beacon error")
    })
    expect(() =>
      trackDataLossNotice("data_loss_notice.learn_more_clicked", PAYLOAD),
    ).not.toThrow()
    // Mirror is still recorded — the queue write happens after dispatch.
    expect(window.__nfmdAnalyticsQueue).toHaveLength(1)
  })

  it("falls back to keepalive fetch when sendBeacon is unavailable", () => {
    Object.defineProperty(navigator, "sendBeacon", {
      configurable: true,
      writable: true,
      value: undefined,
    })
    const mockFetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal("fetch", mockFetch)

    trackDataLossNotice("data_loss_notice.viewed", PAYLOAD)

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit]
    expect(url).toBe("/api/analytics/events")
    expect(init.method).toBe("POST")
    expect(init.keepalive).toBe(true)
    const body = JSON.parse(init.body as string) as { event: string }
    expect(body.event).toBe("data_loss_notice.viewed")
    vi.unstubAllGlobals()
  })
})
