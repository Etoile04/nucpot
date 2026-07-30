/**
 * Tests for hub-api.ts — Hub Node Management API client (NFM-2023).
 *
 * Binds to the B2 contract (NFM-2022): /api/v1/hub/nodes/* returning
 * the ApiResponse envelope with PaginatedResponse for the list route.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal("fetch", mockFetch)
})

const NODE = {
  id: "0b7c9b1e-1111-4222-8333-444455556666",
  hub_node_id: "aaaa1111-2222-4333-8444-555566667777",
  name: "西南所-计算节点",
  node_type: "computing",
  api_endpoint: "https://node.example.org",
  public_key: null,
  status: "active",
  last_heartbeat: "2026-07-30T11:59:30+00:00",
  offline_since: null,
  sync_watermark: "2026-07-30T11:00:00+00:00",
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-30T11:59:30Z",
}

describe("listHubNodes", () => {
  it("calls the list endpoint with pagination params and unwraps the envelope", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        success: true,
        data: { items: [NODE], total: 1, page: 2, per_page: 50, pages: 1 },
      }),
    })

    const { listHubNodes } = await import("@/lib/admin/hub-api")
    const result = await listHubNodes({ page: 2, per_page: 50 })

    const [url] = mockFetch.mock.calls[0]!
    expect(url).toContain("/api/v1/hub/nodes/")
    expect(url).toContain("page=2")
    expect(url).toContain("per_page=50")
    expect(result.items).toHaveLength(1)
    expect(result.items[0]!.name).toBe("西南所-计算节点")
  })

  it("throws the envelope error on success:false", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ success: false, error: "boom" }),
    })

    const { listHubNodes } = await import("@/lib/admin/hub-api")
    await expect(listHubNodes()).rejects.toThrow("boom")
  })
})

describe("registerHubNode", () => {
  it("POSTs the registration payload and returns the created node", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({ success: true, data: NODE }),
    })

    const { registerHubNode } = await import("@/lib/admin/hub-api")
    const created = await registerHubNode({
      hub_node_id: NODE.hub_node_id,
      name: NODE.name,
      node_type: "computing",
      api_endpoint: NODE.api_endpoint,
    })

    const [url, init] = mockFetch.mock.calls[0]!
    expect(url).toContain("/api/v1/hub/nodes/register")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body).name).toBe(NODE.name)
    expect(created.id).toBe(NODE.id)
  })

  it("surfaces FastAPI 422 validation details as readable messages", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 422,
      json: async () => ({
        detail: [
          { loc: ["body", "name"], msg: "name must not be empty", type: "value_error" },
        ],
      }),
    })

    const { registerHubNode } = await import("@/lib/admin/hub-api")
    await expect(
      registerHubNode({
        hub_node_id: NODE.hub_node_id,
        name: "",
        node_type: "computing",
        api_endpoint: NODE.api_endpoint,
      }),
    ).rejects.toThrow("name must not be empty")
  })
})

describe("updateHubNodeStatus", () => {
  it("PUTs the new status to the status endpoint", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ success: true, data: { ...NODE, status: "suspended" } }),
    })

    const { updateHubNodeStatus } = await import("@/lib/admin/hub-api")
    const updated = await updateHubNodeStatus(NODE.id, "suspended")

    const [url, init] = mockFetch.mock.calls[0]!
    expect(url).toContain(`/api/v1/hub/nodes/${NODE.id}/status`)
    expect(init.method).toBe("PUT")
    expect(updated.status).toBe("suspended")
  })
})

describe("deregisterHubNode", () => {
  it("DELETEs the node", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ success: true, data: null }),
    })

    const { deregisterHubNode } = await import("@/lib/admin/hub-api")
    await deregisterHubNode(NODE.id)

    const [url, init] = mockFetch.mock.calls[0]!
    expect(url).toContain(`/api/v1/hub/nodes/${NODE.id}`)
    expect(init.method).toBe("DELETE")
  })
})
