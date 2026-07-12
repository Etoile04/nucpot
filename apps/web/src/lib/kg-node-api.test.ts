/**
 * Tests for the KG Node Detail API client (NFM-1337).
 */

import { describe, it, expect, vi, beforeEach } from "vitest"

// ── Mocks (hoisted so they run before the SUT import) ────────────────

const { fetchMock } = vi.hoisted(() => ({
  fetchMock: vi.fn(),
}))
vi.stubGlobal("fetch", fetchMock)

import { fetchKgNodeDetail } from "@/lib/kg-node-api"

const MOCK_NODE = {
  id: "11111111-1111-1111-1111-111111111111",
  node_type: "Material",
  label: "UO2",
  aliases: ["Uranium Dioxide"],
  properties: { density: "10.97 g/cm³" },
  confidence: 0.92,
  status: "active",
  source_id: "22222222-2222-2222-2222-222222222222",
  corpus_id: null,
  relations: {
    incoming: [
      {
        edge_id: "edge-1",
        relation_type: "references",
        direction: "incoming",
        confidence: 0.8,
        neighbour: {
          id: "pub-1",
          node_type: "Publication",
          label: "Smith 2020",
        },
      },
    ],
    outgoing: [
      {
        edge_id: "edge-2",
        relation_type: "hasProperty",
        direction: "outgoing",
        confidence: 0.95,
        neighbour: {
          id: "prop-1",
          node_type: "Property",
          label: "Melting Point",
        },
      },
    ],
  },
  sources: [
    {
      source_id: "22222222-2222-2222-2222-222222222222",
      figure_id: null,
      label: "Smith 2020",
    },
  ],
}

beforeEach(() => {
  fetchMock.mockReset()
})

describe("fetchKgNodeDetail", () => {
  it("1. calls GET /api/v1/kg/nodes/{type}/{id} with encoded path segments", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_NODE),
    })

    await fetchKgNodeDetail({
      type: "Material",
      id: "11111111-1111-1111-1111-111111111111",
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(
      "/api/v1/kg/nodes/Material/11111111-1111-1111-1111-111111111111",
    )
    expect(init.method ?? "GET").toBe("GET")
  })

  it("2. returns the parsed detail payload", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_NODE),
    })

    const result = await fetchKgNodeDetail({ type: "Material", id: "x" })
    expect(result.id).toBe(MOCK_NODE.id)
    expect(result.node_type).toBe("Material")
    expect(result.label).toBe("UO2")
    expect(result.relations.incoming).toHaveLength(1)
    expect(result.relations.outgoing).toHaveLength(1)
  })

  it("3. rejects with a descriptive error when the API returns non-OK", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: "node not found" }),
    })

    await expect(
      fetchKgNodeDetail({ type: "Material", id: "missing" }),
    ).rejects.toThrow("node not found")
  })

  it("4. rejects synchronously when type or id is missing", async () => {
    await expect(
      fetchKgNodeDetail({ type: "", id: "x" }),
    ).rejects.toThrow("requires both type and id")
    await expect(
      fetchKgNodeDetail({ type: "Material", id: "" }),
    ).rejects.toThrow("requires both type and id")
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("5. percent-encodes special characters in id", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_NODE),
    })

    await fetchKgNodeDetail({ type: "Material", id: "a/b c" })

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe("/api/v1/kg/nodes/Material/a%2Fb%20c")
  })
})