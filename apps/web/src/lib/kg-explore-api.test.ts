import { describe, it, expect, vi, beforeEach } from "vitest"
import { getKgExploreGraph } from "./kg-explore-api"

const mockFetch = vi.fn()

vi.stubGlobal("fetch", mockFetch)

beforeEach(() => {
  vi.clearAllMocks()
})

function mockJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

describe("getKgExploreGraph", () => {
  it("calls GET /api/v1/kg/graph?limit=100 by default", async () => {
    const body = {
      nodes: [{ id: "material:ZrO2", label: "ZrO2", type: "Material" }],
      edges: [
        { source: "material:ZrO2", target: "property:melting_point", type: "has_property" },
      ],
    }
    mockFetch.mockResolvedValueOnce(mockJsonResponse(body))

    const result = await getKgExploreGraph()

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/kg/graph?limit=100",
      expect.any(Object),
    )
    expect(result.nodes[0]!.id).toBe("material:ZrO2")
    expect(result.nodes[0]!.type).toBe("material")
    expect(result.nodes).toHaveLength(1)
    expect(result.edges).toHaveLength(1)
  })

  it("maps Material → material, Property → property, Experiment → entity", async () => {
    const body = {
      nodes: [
        { id: "n1", label: "Steel", type: "Material" },
        { id: "n2", label: "Density", type: "Property" },
        { id: "n3", label: "EXP-001", type: "Experiment" },
        { id: "n4", label: "Source-A", type: "Publication" },
      ],
      edges: [],
    }
    mockFetch.mockResolvedValueOnce(mockJsonResponse(body))

    const result = await getKgExploreGraph()

    const nodes = result.nodes as readonly { type: string }[]
    expect(nodes[0]!.type).toBe("material")
    expect(nodes[1]!.type).toBe("property")
    expect(nodes[2]!.type).toBe("entity")
    expect(nodes[3]!.type).toBe("default")
  })

  it("accepts custom limit parameter", async () => {
    mockFetch.mockResolvedValueOnce(mockJsonResponse({ nodes: [], edges: [] }))

    await getKgExploreGraph(50)

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/kg/graph?limit=50",
      expect.any(Object),
    )
  })

  it("returns empty GraphData on 404 (backend gap)", async () => {
    mockFetch.mockResolvedValueOnce(mockJsonResponse({ detail: "Not found" }, 404))

    const result = await getKgExploreGraph()

    expect(result.nodes).toEqual([])
    expect(result.edges).toEqual([])
  })

  it("returns empty GraphData when nodes array is empty", async () => {
    mockFetch.mockResolvedValueOnce(mockJsonResponse({ nodes: [], edges: [] }))

    const result = await getKgExploreGraph()

    expect(result.nodes).toEqual([])
    expect(result.edges).toEqual([])
  })

  it("throws on non-404 server errors", async () => {
    mockFetch.mockResolvedValueOnce(
      mockJsonResponse({ detail: "Internal Server Error" }, 500),
    )

    await expect(getKgExploreGraph()).rejects.toThrow("Internal Server Error")
  })
})
