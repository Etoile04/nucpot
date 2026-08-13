/**
 * Tests for the KG Node Detail API client (NFM-1337).
 *
 * NOTE: `fetchKgNodeDetail` fans out to TWO fetches in parallel —
 *   GET /api/v1/kg/nodes/{type}/{id}            (base node)
 *   GET /api/v1/kg/nodes/{id}/relations         (edges)
 * and the API client expects each response body wrapped as
 * `{ success: true, data: ... }` (see `request<T>` + `ApiResponse<T>`).
 * The mock below therefore returns a default Response-like value for
 * EVERY fetch call, and each test can override with `mockResolvedValueOnce`
 * when it needs to assert on a specific URL or inject an error.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"

// ── Mocks (hoisted so they run before the SUT import) ────────────────

const { fetchMock, MOCK_NODE, MOCK_RELATIONS, okResponse } = vi.hoisted(() => {
  // Base node payload returned by GET /nodes/{type}/{id}.
  const MOCK_NODE = {
    id: "11111111-1111-1111-1111-111111111111",
    node_type: "Material",
    label: "UO2",
    aliases: ["Uranium Dioxide"],
    properties: { density: "10.97 g/cm³" },
    confidence: 0.92,
    status: "active",
    source_id: "22222222-2222-2222-2222-222222222222",
  }

  // Relations payload returned by GET /nodes/{id}/relations.
  // The focal node is the source of one outgoing edge and the target of
  // one incoming edge so the detail client can bucket both directions.
  const MOCK_RELATIONS = {
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
          confidence: 0.8,
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
          source_id: "22222222-2222-2222-2222-222222222222",
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
          source_id: "22222222-2222-2222-2222-222222222222",
        },
        target_node: {
          id: "prop-1",
          node_type: "Property",
          label: "Melting Point",
          aliases: [],
          properties: {},
          confidence: 0.9,
          status: "active",
          source_id: null,
        },
      },
    ],
    total: 2,
    limit: 50,
    offset: 0,
  }

  // Build a Response-like object the `request<T>` helper can consume.
  const okResponse = (data: unknown) => ({
    ok: true,
    json: () => Promise.resolve({ success: true, data }),
  })

  return {
    fetchMock: vi.fn(),
    MOCK_NODE,
    MOCK_RELATIONS,
    okResponse,
  }
})

vi.stubGlobal("fetch", fetchMock)

import { fetchKgNodeDetail } from "@/lib/kg-node-api"

beforeEach(() => {
  fetchMock.mockReset()
  // Default: every fetch resolves to a 200. Individual tests override
  // with `mockResolvedValueOnce` when they need a specific URL/error.
  fetchMock.mockResolvedValue(okResponse({}))
})

describe("fetchKgNodeDetail", () => {
  it("1. calls GET /api/v1/kg/nodes/{type}/{id} with encoded path segments", async () => {
    // First fetch is the base node; second is relations.
    fetchMock
      .mockResolvedValueOnce(okResponse(MOCK_NODE))
      .mockResolvedValueOnce(okResponse(MOCK_RELATIONS))

    await fetchKgNodeDetail({
      type: "Material",
      id: "11111111-1111-1111-1111-111111111111",
    })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [url1, init1] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url1).toBe(
      "/api/v1/kg/nodes/Material/11111111-1111-1111-1111-111111111111",
    )
    expect(init1.method ?? "GET").toBe("GET")
  })

  it("2. returns the parsed detail payload", async () => {
    fetchMock
      .mockResolvedValueOnce(okResponse(MOCK_NODE))
      .mockResolvedValueOnce(okResponse(MOCK_RELATIONS))

    const result = await fetchKgNodeDetail({
      type: "Material",
      id: "11111111-1111-1111-1111-111111111111",
    })
    expect(result.id).toBe(MOCK_NODE.id)
    expect(result.node_type).toBe("Material")
    expect(result.label).toBe("UO2")
    expect(result.relations.incoming).toHaveLength(1)
    expect(result.relations.outgoing).toHaveLength(1)
  })

  it("3. rejects with a descriptive error when the API returns non-OK", async () => {
    const notFound = {
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: "node not found" }),
    }
    fetchMock.mockResolvedValueOnce(notFound)

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
    fetchMock
      .mockResolvedValueOnce(okResponse(MOCK_NODE))
      .mockResolvedValueOnce(okResponse(MOCK_RELATIONS))

    await fetchKgNodeDetail({ type: "Material", id: "a/b c" })

    const [url1] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url1).toBe("/api/v1/kg/nodes/Material/a%2Fb%20c")
  })
})