import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { SvgRenderer } from "../SvgRenderer"
import type { SimNode, SimEdge, GraphViewport, GraphSelection } from "../types"

/* ------------------------------------------------------------------ */
/*  Test data                                                         */
/* ------------------------------------------------------------------ */

function makeSimNode(overrides: Partial<SimNode> = {}): SimNode {
  return {
    id: "n1",
    label: "Uranium",
    category: "material",
    radius: 8,
    x: 100,
    y: 200,
    fx: null,
    fy: null,
    ...overrides,
  }
}

const NODES: SimNode[] = [
  makeSimNode({ id: "n1", label: "Uranium", category: "material", x: 100, y: 200 }),
  makeSimNode({ id: "n2", label: "Density", category: "property", x: 200, y: 300 }),
]

const EDGES: SimEdge[] = [
  {
    id: "e1",
    source: NODES[0]!,
    target: NODES[1]!,
    weight: 1,
  },
]

const VIEWPORT: GraphViewport = { x: 0, y: 0, k: 1 }
const EMPTY_SELECTION: GraphSelection = { nodeId: null, hoveredId: null }

const SVG_REF = { current: null } as React.RefObject<SVGSVGElement | null>

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */

function getAllNodeLabels(container: HTMLElement): SVGTextElement[] {
  return Array.from(container.querySelectorAll("text.graph-node-label"))
}

function getEdgeLines(container: HTMLElement): SVGLineElement[] {
  return Array.from(container.querySelectorAll("line"))
}

/* ------------------------------------------------------------------ */
/*  Tests                                                             */
/* ------------------------------------------------------------------ */

describe("SvgRenderer", () => {
  /* ----- Basic rendering ----- */

  it("renders nodes with ARIA labels", () => {
    render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={EMPTY_SELECTION}
      />,
    )

    expect(screen.getByRole("button", { name: /Node: Uranium/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Node: Density/i })).toBeInTheDocument()
  })

  it("renders edges as line elements", () => {
    const { container } = render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={EMPTY_SELECTION}
      />,
    )

    const lines = getEdgeLines(container)
    expect(lines).toHaveLength(1)
    expect(lines[0]!.getAttribute("x1")).toBe("100")
    expect(lines[0]!.getAttribute("y1")).toBe("200")
    expect(lines[0]!.getAttribute("x2")).toBe("200")
    expect(lines[0]!.getAttribute("y2")).toBe("300")
  })

  /* ----- Viewport transform ----- */

  it("applies viewport transform", () => {
    const { container } = render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={EMPTY_SELECTION}
      />,
    )

    const viewportGroup = container.querySelector(".graph-viewport")
    expect(viewportGroup?.getAttribute("transform")).toBe(
      "translate(0, 0) scale(1)",
    )
  })

  it("applies custom viewport transform", () => {
    const viewport: GraphViewport = { x: 50, y: 100, k: 1.5 }

    const { container } = render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={viewport}
        selection={EMPTY_SELECTION}
      />,
    )

    const viewportGroup = container.querySelector(".graph-viewport")
    expect(viewportGroup?.getAttribute("transform")).toBe(
      "translate(50, 100) scale(1.5)",
    )
  })

  /* ----- Node category colors ----- */

  it("renders nodes with category-based fill colors", () => {
    const { container } = render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={EMPTY_SELECTION}
      />,
    )

    const circles = container.querySelectorAll("circle.graph-node-circle")
    expect(circles).toHaveLength(2)
    // material → #34d399
    expect(circles[0]!.getAttribute("fill")).toBe("#34d399")
    // property → #fbbf24
    expect(circles[1]!.getAttribute("fill")).toBe("#fbbf24")
  })

  /* ----- Zoom-level labels (NFM-1149 AC) ----- */

  it("shows labels when zoom level k > 0.8", () => {
    const viewport: GraphViewport = { x: 0, y: 0, k: 1.0 }

    const { container } = render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={viewport}
        selection={EMPTY_SELECTION}
      />,
    )

    const labels = getAllNodeLabels(container)
    expect(labels).toHaveLength(2)
  })

  it("hides labels when zoom level k <= 0.8", () => {
    const viewport: GraphViewport = { x: 0, y: 0, k: 0.8 }

    const { container } = render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={viewport}
        selection={EMPTY_SELECTION}
      />,
    )

    const labels = getAllNodeLabels(container)
    expect(labels).toHaveLength(0)
  })

  /* ----- Hover: 1-hop neighborhood ----- */

  it("dims non-neighbor nodes when a node is hovered", () => {
    const selection: GraphSelection = { nodeId: null, hoveredId: "n1" }

    const { container } = render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={selection}
      />,
    )

    const nodeGroups = container.querySelectorAll("[role='button']")
    const n1 = Array.from(nodeGroups).find(
      (g) => g.getAttribute("aria-label") === "Node: Uranium",
    )
    const n2 = Array.from(nodeGroups).find(
      (g) => g.getAttribute("aria-label") === "Node: Density",
    )

    // Hovered node (n1) should not be dimmed (opacity via inline style)
    expect(n1?.getAttribute("style")).toContain("opacity: 1")
    // n2 IS a 1-hop neighbor, should not be dimmed
    expect(n2?.getAttribute("style")).toContain("opacity: 1")
  })

  it("highlights edges connected to hovered node", () => {
    const selection: GraphSelection = { nodeId: null, hoveredId: "n1" }

    const { container } = render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={selection}
      />,
    )

    const lines = getEdgeLines(container)
    expect(lines).toHaveLength(1)
    // Edge is connected to hovered node → highlighted
    expect(lines[0]!.getAttribute("stroke")).toBe("#3b82f6")
    expect(lines[0]!.getAttribute("stroke-width")).toBe("2")
  })

  it("dims isolated nodes not in hovered neighborhood", () => {
    const n3 = makeSimNode({
      id: "n3",
      label: "Isolated",
      category: "unknown",
      x: 300,
      y: 400,
    })
    const selection: GraphSelection = { nodeId: null, hoveredId: "n1" }

    const { container } = render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={[...NODES, n3]}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={selection}
      />,
    )

    const nodeGroups = container.querySelectorAll("[role='button']")
    const isolated = Array.from(nodeGroups).find(
      (g) => g.getAttribute("aria-label") === "Node: Isolated",
    )
    // Isolated node should be dimmed (not in 1-hop of n1) — opacity via inline style
    expect(isolated?.getAttribute("style")).toContain("opacity: 0.2")
  })

  /* ----- Selection ring ----- */

  it("renders selection ring on selected node", () => {
    const selection: GraphSelection = { nodeId: "n1", hoveredId: null }

    const { container } = render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={selection}
      />,
    )

    const selectionRing = container.querySelector(".graph-selection-ring")
    expect(selectionRing).toBeInTheDocument()
    expect(selectionRing?.getAttribute("cx")).toBe("100")
    expect(selectionRing?.getAttribute("cy")).toBe("200")
    expect(selectionRing?.getAttribute("stroke-width")).toBe("2.5")
  })

  /* ----- Hover glow ----- */

  it("renders hover glow on hovered node", () => {
    const selection: GraphSelection = { nodeId: null, hoveredId: "n2" }

    const { container } = render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={selection}
      />,
    )

    const glow = container.querySelector(".graph-hover-glow")
    expect(glow).toBeInTheDocument()
    expect(glow?.getAttribute("cx")).toBe("200")
    expect(glow?.getAttribute("cy")).toBe("300")
    expect(glow?.getAttribute("opacity")).toBe("0.12")
  })

  /* ----- Hover callback ----- */

  it("calls onNodeHover(null) on mouse leave", () => {
    const onNodeHover = vi.fn()
    const selection: GraphSelection = { nodeId: null, hoveredId: "n2" }

    render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={selection}
        onNodeHover={onNodeHover}
      />,
    )

    const hoveredNode = screen.getByRole("button", { name: /Node: Density/i })
    expect(hoveredNode).toBeInTheDocument()

    fireEvent.mouseLeave(hoveredNode)
    expect(onNodeHover).toHaveBeenCalledWith(null)
  })

  it("calls onNodeClick when a node is clicked", () => {
    const onNodeClick = vi.fn()

    render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={EMPTY_SELECTION}
        onNodeClick={onNodeClick}
      />,
    )

    const node = screen.getByRole("button", { name: /Node: Uranium/i })
    fireEvent.click(node)
    expect(onNodeClick).toHaveBeenCalledTimes(1)
    expect(onNodeClick).toHaveBeenCalledWith(
      expect.objectContaining({ id: "n1", label: "Uranium" }),
    )
  })

  /* ----- Keyboard accessibility ----- */

  it("nodes are keyboard-focusable with tabIndex=0", () => {
    render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={EMPTY_SELECTION}
      />,
    )

    const nodes = screen.getAllByRole("button")
    for (const node of nodes) {
      expect(node).toHaveAttribute("tabindex", "0")
    }
  })

  it("renders keyboard focus ring on focused node", () => {
    render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={EMPTY_SELECTION}
      />,
    )

    const node = screen.getByRole("button", { name: /Node: Uranium/i })
    fireEvent.focus(node)

    // Focused node should have visible focus ring via outline
    const style = node.getAttribute("style") ?? ""
    expect(style).toContain("outline")
  })

  /* ----- CSS transitions ----- */

  it("applies CSS transitions on node groups for smooth opacity changes", () => {
    const { container } = render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={EMPTY_SELECTION}
      />,
    )

    const nodeGroup = container.querySelector("[role='button']")
    const style = nodeGroup?.getAttribute("style") ?? ""
    expect(style).toContain("transition")
  })

  it("applies CSS transitions on edge lines", () => {
    const { container } = render(
      <SvgRenderer
        svgRef={SVG_REF}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={EMPTY_SELECTION}
      />,
    )

    const line = container.querySelector("line")
    const style = line?.getAttribute("style") ?? ""
    expect(style).toContain("transition")
  })
})
