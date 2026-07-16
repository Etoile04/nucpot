import { describe, it, expect, vi, beforeEach } from "vitest"

/**
 * Pure-function coverage for the NFM-1258 subgraph mapping helpers
 * and the getMaterialSubgraph fetch wrapper.
 */

describe("toGraphNodeType", () => {
  it("maps Material / Property / Experiment / Condition / Publication correctly", async () => {
    const { toGraphNodeType } = await import("./materials-api")

    expect(toGraphNodeType("Material")).toBe("material")
    expect(toGraphNodeType("Property")).toBe("property")
    expect(toGraphNodeType("Experiment")).toBe("entity")
    expect(toGraphNodeType("Condition")).toBe("default")
    expect(toGraphNodeType("Publication")).toBe("default")
    expect(toGraphNodeType("weird-thing")).toBe("default")
  })

  it("is case-insensitive", async () => {
    const { toGraphNodeType } = await import("./materials-api")
    expect(toGraphNodeType("material")).toBe("material")
    expect(toGraphNodeType("PROPERTY")).toBe("property")
  })
})

describe("mapSubgraphResponse", () => {
  it("preserves node ids verbatim", async () => {
    const { mapSubgraphResponse } = await import("./materials-api")

    const result = mapSubgraphResponse({
      nodes: [
        { id: "material:ZrO2", label: "ZrO2", type: "Material" },
        { id: "property:density", label: "Density", type: "Property" },
      ],
      edges: [],
    })

    expect(result.nodes[0]?.id).toBe("material:ZrO2")
    expect(result.nodes[1]?.id).toBe("property:density")
  })

  it("maps node types via toGraphNodeType", async () => {
    const { mapSubgraphResponse } = await import("./materials-api")

    const result = mapSubgraphResponse({
      nodes: [
        { id: "a", label: "A", type: "Material" },
        { id: "b", label: "B", type: "Property" },
        { id: "c", label: "C", type: "Experiment" },
      ],
      edges: [],
    })

    expect(result.nodes.map((n) => n.type)).toEqual([
      "material",
      "property",
      "entity",
    ])
  })

  it("synthesises stable edge ids", async () => {
    const { mapSubgraphResponse } = await import("./materials-api")

    const result = mapSubgraphResponse({
      nodes: [
        { id: "x", label: "X", type: "Material" },
        { id: "y", label: "Y", type: "Property" },
      ],
      edges: [
        { source: "x", target: "y", type: "X" },
        { source: "y", target: "x", type: "Y" },
      ],
    })

    expect(result.edges).toHaveLength(2)
    expect(result.edges[0]?.id).toMatch(/^e-0-x->y$/)
    expect(result.edges[1]?.id).toMatch(/^e-1-y->x$/)
  })
})

describe("getMaterialSubgraph fetch wrapper", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("hits /api/v1/kg/graph with nodeId + depth", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ nodes: [], edges: [] }),
    })
    global.fetch = fetchMock as unknown as typeof fetch

    const { getMaterialSubgraph } = await import("./materials-api")

    await getMaterialSubgraph("ZrO2", 2)

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/kg/graph?nodeId=ZrO2&depth=2",
      expect.objectContaining({
        headers: expect.objectContaining({
          "Content-Type": "application/json",
        }),
      }),
    )
  })

  it("throws on non-OK response with parsed detail", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: "boom" }),
    }) as unknown as typeof fetch

    const { getMaterialSubgraph } = await import("./materials-api")

    await expect(getMaterialSubgraph("ZrO2")).rejects.toThrow(/boom/)
  })

  it("defaults depth to 2", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ nodes: [], edges: [] }),
    })
    global.fetch = fetchMock as unknown as typeof fetch

    const { getMaterialSubgraph } = await import("./materials-api")

    await getMaterialSubgraph("ZrO2")

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/kg/graph?nodeId=ZrO2&depth=2",
      expect.any(Object),
    )
  })
})

// ── NFM-1067 envelope unwrap regression ────────────────────────────────
// Backend wraps every response in ApiResponse[T] = { success, data: T, error? }.
// The shared `request()` helper does NOT auto-unwrap, so each endpoint
// helper in materials-api must destructure `envelope.data` itself and
// return the inner shape — otherwise consumers read `.meta` / `.name`
// off the envelope and crash with `undefined.total` etc.

describe("getMaterialProperties envelope unwrap (NFM-1067)", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("returns the inner {data, meta} shape, NOT the {success, data, error} envelope", async () => {
    const inner = {
      data: [
        {
          id: "prop-1",
          name: "密度",
          value: "5.68",
          unit: "g/cm³",
          source: "J. Nucl. Mater.",
          confidence: 0.92,
        },
      ],
      meta: { total: 1, page: 1, limit: 50 },
    }
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ success: true, data: inner, error: null }),
    }) as unknown as typeof fetch

    const { getMaterialProperties } = await import("./materials-api")

    const result = await getMaterialProperties("mat-1", { page: 1, limit: 50 })

    // The returned object must be the inner list shape, not the envelope.
    expect(result).toEqual(inner)
    expect(result).not.toHaveProperty("success")
    expect(result).not.toHaveProperty("error")
    // Sanity: the consumer reads .meta.total and .data (array) directly.
    expect(result.meta.total).toBe(1)
    expect(Array.isArray(result.data)).toBe(true)
    expect(result.data[0]?.name).toBe("密度")
  })

  it("hits /api/v1/materials/{id}/properties with query params", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        success: true,
        data: { data: [], meta: { total: 0, page: 1, limit: 50 } },
        error: null,
      }),
    })
    global.fetch = fetchMock as unknown as typeof fetch

    const { getMaterialProperties } = await import("./materials-api")

    await getMaterialProperties("mat-1", {
      page: 2,
      limit: 25,
      sort: "name",
      order: "desc",
      filter: "density",
    })

    const calledUrl = fetchMock.mock.calls[0]?.[0] as string
    expect(calledUrl).toContain("/api/v1/materials/mat-1/properties")
    expect(calledUrl).toContain("page=2")
    expect(calledUrl).toContain("limit=25")
    expect(calledUrl).toContain("sort=name")
    expect(calledUrl).toContain("order=desc")
    expect(calledUrl).toContain("filter=density")
  })
})

describe("getMaterial envelope unwrap (NFM-1067)", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("returns the inner MaterialSummary shape, NOT the envelope", async () => {
    const inner = { id: "mat-1", name: "二氧化锆", formula: "ZrO2" }
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ success: true, data: inner, error: null }),
    }) as unknown as typeof fetch

    const { getMaterial } = await import("./materials-api")

    const result = await getMaterial("mat-1")

    expect(result).toEqual(inner)
    expect(result).not.toHaveProperty("success")
    expect(result.name).toBe("二氧化锆")
    expect(result.formula).toBe("ZrO2")
  })
})