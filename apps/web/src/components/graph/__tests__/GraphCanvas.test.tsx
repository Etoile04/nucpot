import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { GraphCanvas } from "../GraphCanvas"
import type { GraphData } from "../types"

/* ------------------------------------------------------------------ */
/*  Polyfill ResizeObserver for jsdom                                   */
/* ------------------------------------------------------------------ */

class MockResizeObserver {
  readonly observe = vi.fn()
  readonly unobserve = vi.fn()
  readonly disconnect = vi.fn()
}

if (typeof window !== "undefined" && !("ResizeObserver" in window)) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (window as any).ResizeObserver = MockResizeObserver
}

/* ------------------------------------------------------------------ */
/*  Mock d3-force so the hook initializes synchronously                */
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
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2" },
  ],
}

const EXPANDABLE_DATA: GraphData = {
  nodes: [
    { id: "n1", label: "Uranium", type: "material", childCount: 3 },
    { id: "n2", label: "Density", type: "property" },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2" },
  ],
}

function makeLargeData(count: number): GraphData {
  return {
    nodes: Array.from({ length: count }, (_, i) => ({
      id: `n${i}`,
      label: `Node ${i}`,
      type: "default" as const,
    })),
    edges: Array.from({ length: count - 1 }, (_, i) => ({
      id: `e${i}`,
      source: `n${i}`,
      target: `n${i + 1}`,
    })),
  }
}

describe("GraphCanvas", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders SVG renderer for <200 nodes", () => {
    render(<GraphCanvas data={SMALL_DATA} />)

    const svg = screen.getByRole("img", { name: /knowledge graph/i })
    expect(svg).toBeInTheDocument()
    expect(svg.tagName.toLowerCase()).toBe("svg")
  })

  it("renders Canvas renderer for >=200 nodes", () => {
    const largeData = makeLargeData(200)
    render(<GraphCanvas data={largeData} />)

    const canvas = screen.getByTestId("graph-canvas")
    expect(canvas).toBeInTheDocument()
    expect(canvas.tagName.toLowerCase()).toBe("canvas")
  })

  it("calls onNodeClick when a node is clicked", () => {
    const onNodeClick = vi.fn()
    render(<GraphCanvas data={SMALL_DATA} onNodeClick={onNodeClick} />)

    const node = screen.getByRole("button", { name: /Node: Uranium/i })
    fireEvent.click(node)

    expect(onNodeClick).toHaveBeenCalledOnce()
    const clickedNode = onNodeClick.mock.calls[0]![0]
    expect(clickedNode.id).toBe("n1")
    expect(clickedNode.label).toBe("Uranium")
  })

  it("shows empty state when no nodes", () => {
    const emptyData: GraphData = { nodes: [], edges: [] }
    render(<GraphCanvas data={emptyData} />)

    expect(screen.getByRole("status")).toHaveTextContent(
      "No graph data to display",
    )
  })

  it("applies custom className", () => {
    render(<GraphCanvas data={SMALL_DATA} className="custom-graph" />)

    const container = screen.getByRole("application")
    expect(container.className).toContain("custom-graph")
  })

  it("renders with role=application and aria-label", () => {
    render(<GraphCanvas data={SMALL_DATA} />)

    const container = screen.getByRole("application", {
      name: /interactive knowledge graph/i,
    })
    expect(container).toBeInTheDocument()
  })

  it("shows loading state during simulation", () => {
    render(<GraphCanvas data={SMALL_DATA} />)

    const loading = screen.getByRole("status", { busy: true })
    expect(loading).toBeInTheDocument()
    expect(loading).toHaveTextContent("Computing layout")
  })

  it("calls onExpand on double-click when childCount > 0", () => {
    const onExpand = vi.fn()
    render(
      <GraphCanvas data={EXPANDABLE_DATA} onExpand={onExpand} />,
    )

    const node = screen.getByRole("button", { name: /Node: Uranium/i })
    fireEvent.dblClick(node)

    expect(onExpand).toHaveBeenCalledOnce()
    const expandedNode = onExpand.mock.calls[0]![0]
    expect(expandedNode.id).toBe("n1")
    expect(expandedNode.childCount).toBe(3)
  })

  it("does not call onExpand on double-click when childCount is 0", () => {
    const onExpand = vi.fn()
    render(
      <GraphCanvas data={SMALL_DATA} onExpand={onExpand} />,
    )

    const node = screen.getByRole("button", { name: /Node: Uranium/i })
    fireEvent.dblClick(node)

    expect(onExpand).not.toHaveBeenCalled()
  })
})
