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

// ── Mock data matching the two-endpoint contract ────────────────────

const MOCK_NODE_RESPONSE = {
  success: true,
  data: {
    id: "11111111-1111-1111-1111-111111111111",
    node_type: "Material",
    label: "UO2",
    aliases: ["Uranium Dioxide"],
    properties: { density: "10.97 g/cm³" },
    confidence: 0.92,
    status: "active",
    source_id: "22222222-2222-2222-2222-222222222222",
  },
}

const MOCK_RELATIONS_RESPONSE = {
  success: true,
  data: {
    items: [
      {
        id: "edge-1",
        relation_type: "references",
        confidence: 0.8,
        properties: {},
        source_node: {
          id: "pub-1",
          node_type: "Publication",
          label: "Smith 2020",
          aliases: [],
          properties: {},
          confidence: 0.7,
          status: "active",
          source_id: null,
        },
        target_node: {
          id: "11111111-1111-1111-1111-111111111111",
          node_type: "Material",
          label: "UO2",
          aliases: [],
          properties: {},
          confidence: 0.92,
          status: "active",
          source_id: null,
        },
      },
      {
        id: "edge-2",
        relation_type: "hasProperty",
        confidence: 0.95,
        properties: {},
        source_node: {
          id: "11111111-1111-1111-1111-111111111111",
          node_type: "Material",
          label: "UO2",
          aliases: [],
          properties: {},
          confidence: 0.92,
          status: "active",
          source_id: null,
        },
        target_node: {
          id: "prop-1",
          node_type: "Property",
          label: "Melting Point",
          aliases: [],
          properties: {},
          confidence: 0.85,
          status: "active",
          source_id: null,
        },
      },
    ],
    total: 2,
    limit: 50,
    offset: 0,
  },
}

/** Queue both fetch responses (node + relations). */
function mockSuccessResponses() {
  fetchMock
    .mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_NODE_RESPONSE),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_RELATIONS_RESPONSE),
    })
}

beforeEach(() => {
  fetchMock.mockReset()
})

describe("fetchKgNodeDetail", () => {
  it("1. calls GET /api/v1/kg/nodes/{type}/{id} and GET /api/v1/kg/nodes/{id}/relations in parallel", async () => {
    mockSuccessResponses()

    await fetchKgNodeDetail({
      type: "Material",
      id: "11111111-1111-1111-1111-111111111111",
    })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    const urls = fetchMock.mock.calls.map(([u]) => u as string)
    expect(urls).toContain(
      "/api/v1/kg/nodes/Material/11111111-1111-1111-1111-111111111111",
    )
    expect(urls).toContain(
      "/api/v1/kg/nodes/11111111-1111-1111-1111-111111111111/relations?limit=50&offset=0",
    )
  })

  it("2. returns the combined detail payload with incoming/outgoing relations", async () => {
    mockSuccessResponses()

    const result = await fetchKgNodeDetail({ type: "Material", id: "x" })
    expect(result.id).toBe("11111111-1111-1111-1111-111111111111")
    expect(result.node_type).toBe("Material")
    expect(result.label).toBe("UO2")
    // edge-1: target is the focal node → incoming
    expect(result.relations.incoming).toHaveLength(1)
    expect(result.relations.incoming[0]!.relation_type).toBe("references")
    // edge-2: source is the focal node → outgoing
    expect(result.relations.outgoing).toHaveLength(1)
    expect(result.relations.outgoing[0]!.relation_type).toBe("hasProperty")
  })

  it("3. rejects with a descriptive error when the API returns non-OK", async () => {
    fetchMock
      .mockResolvedValueOnce({
        ok: false,
        status: 404,
        json: () => Promise.resolve({ detail: "node not found" }),
      })
      // Second fetch for relations is also needed (Promise.all)
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(MOCK_RELATIONS_RESPONSE),
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
    mockSuccessResponses()

    await fetchKgNodeDetail({ type: "Material", id: "a/b c" })

    const urls = fetchMock.mock.calls.map(([u]) => u as string)
    expect(urls[0]).toBe("/api/v1/kg/nodes/Material/a%2Fb%20c")
  })
})
