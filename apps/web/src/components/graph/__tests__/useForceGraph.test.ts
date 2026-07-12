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
    source: `n${i}`,
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
})
