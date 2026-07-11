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