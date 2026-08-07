import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act, waitFor } from "@testing-library/react"
import { useForceGraph } from "../useForceGraph"
import type { GraphData, GraphViewport } from "../types"

/* ------------------------------------------------------------------ */
/*  Mock d3-force to avoid dynamic import complexity in tests         */
/* ------------------------------------------------------------------ */

const chainLink = {
  id: vi.fn(() => chainLink),
  distance: vi.fn(() => chainLink),
}

const chainCollide = {
  radius: vi.fn(() => chainCollide),
}

const mockSimulation = {
  stop: vi.fn(),
  restart: vi.fn(),
  alpha: vi.fn(() => mockSimulation),
  alphaDecay: vi.fn(() => mockSimulation),
  on: vi.fn(() => mockSimulation),
  force: vi.fn(() => mockSimulation),
}

vi.mock("d3-force", () => ({
  forceSimulation: vi.fn(() => mockSimulation),
  forceLink: vi.fn(() => chainLink),
  forceManyBody: vi.fn(() => ({ strength: vi.fn() })),
  forceCenter: vi.fn(() => vi.fn()),
  forceCollide: vi.fn(() => chainCollide),
}))

/* ------------------------------------------------------------------ */
/*  Test data                                                         */
/* ------------------------------------------------------------------ */

const SMALL_DATA: GraphData = {
  nodes: [
    { id: "n1", label: "Uranium", type: "material" },
    { id: "n2", label: "Density", type: "property" },
    { id: "n3", label: "EAM", type: "default" },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2" },
    { id: "e2", source: "n1", target: "n3" },
  ],
}

const LARGE_DATA: GraphData = {
  nodes: Array.from({ length: 250 }, (_, i) => ({
    id: `n${i}`,
    label: `Node ${i}`,
    type: "default" as const,
  })),
  edges: Array.from({ length: 300 }, (_, i) => ({
    id: `e${i}`,
    source: `n${i % 250}`,
    target: `n${(i + 1) % 250}`,
  })),
}

describe("useForceGraph", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("initializes simulation with nodes and edges", () => {
    const { result } = renderHook(() =>
      useForceGraph(SMALL_DATA, 800, 600),
    )

    expect(result.current.simNodes).toHaveLength(3)
    expect(result.current.simEdges).toHaveLength(2)
    expect(result.current.isRunning).toBe(true)
    expect(result.current.viewport).toEqual({ x: 0, y: 0, k: 1 })
    expect(result.current.selection).toEqual({ nodeId: null, hoveredId: null })
  })

  it("selects a node by id", () => {
    const { result } = renderHook(() =>
      useForceGraph(SMALL_DATA, 800, 600),
    )

    act(() => {
      result.current.selectNode("n1")
    })

    expect(result.current.selection.nodeId).toBe("n1")
  })

  it("hovers a node by id", () => {
    const { result } = renderHook(() =>
      useForceGraph(SMALL_DATA, 800, 600),
    )

    act(() => {
      result.current.hoverNode("n2")
    })

    expect(result.current.selection.hoveredId).toBe("n2")

    act(() => {
      result.current.hoverNode(null)
    })

    expect(result.current.selection.hoveredId).toBe(null)
  })

  it("zoomTo updates viewport k", () => {
    const { result } = renderHook(() =>
      useForceGraph(SMALL_DATA, 800, 600),
    )

    act(() => {
      result.current.zoomTo(2.5)
    })

    expect(result.current.viewport.k).toBe(2.5)
  })

  it("fitToView resets viewport to origin", () => {
    const { result } = renderHook(() =>
      useForceGraph(SMALL_DATA, 800, 600),
    )

    act(() => {
      result.current.zoomTo(3)
    })

    act(() => {
      result.current.fitToView()
    })

    expect(result.current.viewport).toEqual({ x: 0, y: 0, k: 1 })
  })

  it("setViewport updates viewport x, y, k", () => {
    const { result } = renderHook(() =>
      useForceGraph(SMALL_DATA, 800, 600),
    )

    const newViewport: GraphViewport = { x: 50, y: 100, k: 1.5 }

    act(() => {
      result.current.setViewport(newViewport)
    })

    expect(result.current.viewport).toEqual(newViewport)
  })

  it("restart re-heats the simulation", async () => {
    const { result } = renderHook(() =>
      useForceGraph(SMALL_DATA, 800, 600),
    )

    // Wait for the async createSimulation to resolve and set simRef
    await waitFor(() => {
      expect(result.current.simNodes).toHaveLength(3)
    })

    act(() => {
      result.current.restart()
    })

    expect(mockSimulation.alpha).toHaveBeenCalledWith(1)
    expect(mockSimulation.restart).toHaveBeenCalled()
  })

  it("handles large datasets (250 nodes)", () => {
    const { result } = renderHook(() =>
      useForceGraph(LARGE_DATA, 800, 600),
    )

    expect(result.current.simNodes).toHaveLength(250)
    expect(result.current.simEdges).toHaveLength(300)
    expect(result.current.isRunning).toBe(true)
  })

  it("clears isRunning when d3-force setup throws after dynamic import (NFM-2608)", async () => {
    // Simulate the canary-2026-08-07 regression: the dynamic import of
    // d3-force succeeds but a transitive API call (e.g. forceLink) rejects.
    // createSimulation() is async, so the throw becomes a rejected promise.
    // The hook must NOT leave isRunning stuck on true — otherwise the
    // GraphCanvas overlay hangs on "Computing layout…" indefinitely.
    const { forceLink } = await import("d3-force")
    vi.mocked(forceLink).mockImplementationOnce(() => {
      throw new Error("d3-force transitive API missing")
    })

    const { result } = renderHook(() =>
      useForceGraph(SMALL_DATA, 800, 600),
    )

    // Initially the hook sets isRunning=true while waiting for the simulation.
    expect(result.current.isRunning).toBe(true)

    // Give the rejected promise a chance to settle.
    await waitFor(() => {
      expect(result.current.isRunning).toBe(false)
    })
    expect(result.current.error).toBeInstanceOf(Error)
    expect(result.current.error?.message).toContain("d3-force transitive API missing")
  })

  it("does not set isRunning for empty data (NFM-2608 empty-data guard)", () => {
    const EMPTY_DATA: GraphData = { nodes: [], edges: [] }

    const { result } = renderHook(() =>
      useForceGraph(EMPTY_DATA, 800, 600),
    )

    // Must NOT enter running state — createSimulation returns null for
    // empty data and the old code never cleared isRunning.
    expect(result.current.isRunning).toBe(false)
    expect(result.current.error).toBeNull()
    expect(result.current.simNodes).toHaveLength(0)
    expect(result.current.simEdges).toHaveLength(0)
  })

  it("filters out edges with dangling source/target node references (NFM-2616)", () => {
    // The backend may return edges that reference nodes no longer in the
    // graph (e.g. deleted nodes whose edges weren't cascaded).  d3-force
    // would throw "node not found" for these — the hook must silently
    // drop them instead.
    const DANGLING_EDGE_DATA: GraphData = {
      nodes: [
        { id: "n1", label: "Uranium", type: "material" },
        { id: "n2", label: "Density", type: "property" },
      ],
      edges: [
        { id: "e1", source: "n1", target: "n2" },
        { id: "e2", source: "dead-node-uuid", target: "n1" },
        { id: "e3", source: "n2", target: "another-missing-uuid" },
      ],
    }

    const { result } = renderHook(() =>
      useForceGraph(DANGLING_EDGE_DATA, 800, 600),
    )

    // Only the valid edge (n1→n2) should survive; two dangling edges dropped.
    expect(result.current.simNodes).toHaveLength(2)
    expect(result.current.simEdges).toHaveLength(1)
    expect(result.current.simEdges[0]?.id).toBe("e1")

    // No error — dangling edges are silently discarded.
    expect(result.current.error).toBeNull()
  })

  it("filters out all edges when every edge has a dangling reference (NFM-2616)", () => {
    const ALL_DANGLING: GraphData = {
      nodes: [{ id: "n1", label: "Solo", type: "default" }],
      edges: [
        { id: "e1", source: "ghost-a", target: "n1" },
        { id: "e2", source: "n1", target: "ghost-b" },
      ],
    }

    const { result } = renderHook(() =>
      useForceGraph(ALL_DANGLING, 800, 600),
    )

    expect(result.current.simNodes).toHaveLength(1)
    expect(result.current.simEdges).toHaveLength(0)
    expect(result.current.error).toBeNull()
  })
})
