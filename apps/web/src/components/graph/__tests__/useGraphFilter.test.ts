import { describe, it, expect } from "vitest"
import { renderHook } from "@testing-library/react"
import { useGraphFilter } from "../useGraphFilter"
import type { GraphData } from "../types"

const SAMPLE_DATA: GraphData = {
  nodes: [
    { id: "1", label: "Uranium", type: "material" },
    { id: "2", label: "Density", type: "property" },
    { id: "3", label: "Phase", type: "entity" },
    { id: "4", label: "Other", type: "default" },
  ],
  edges: [
    { id: "e1", source: "1", target: "2", label: "has property" },
    { id: "e2", source: "1", target: "3" },
  ],
}

describe("useGraphFilter", () => {
  it("returns all data when no filter active", () => {
    const { result } = renderHook(() => useGraphFilter(SAMPLE_DATA))
    expect(result.current.filteredData.nodes).toHaveLength(4)
    expect(result.current.filteredData.edges).toHaveLength(2)
  })

  it("filters nodes by active type", () => {
    const { result } = renderHook(() =>
      useGraphFilter(SAMPLE_DATA, {
        activeTypes: new Set(["material"]),
      }),
    )
    expect(result.current.filteredData.nodes).toHaveLength(1)
    expect(result.current.filteredData.nodes[0]!.id).toBe("1")
    // No edges because node 2/3 are filtered out
    expect(result.current.filteredData.edges).toHaveLength(0)
  })

  it("filters edges to only those between visible nodes", () => {
    const { result } = renderHook(() =>
      useGraphFilter(SAMPLE_DATA, {
        activeTypes: new Set(["material", "property"]),
      }),
    )
    expect(result.current.filteredData.nodes).toHaveLength(2)
    expect(result.current.filteredData.edges).toHaveLength(1)
    expect(result.current.filteredData.edges[0]!.id).toBe("e1")
  })

  it("exposes allTypes list", () => {
    const { result } = renderHook(() => useGraphFilter(SAMPLE_DATA))
    expect(result.current.allTypes).toContain("material")
    expect(result.current.allTypes).toContain("property")
    expect(result.current.allTypes).toContain("entity")
    expect(result.current.allTypes).toContain("default")
    expect(result.current.allTypes).toHaveLength(4)
  })
})
