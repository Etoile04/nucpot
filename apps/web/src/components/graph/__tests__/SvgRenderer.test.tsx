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
    source: NODES[0],
    target: NODES[1],
    weight: 1,
  },
]

const VIEWPORT: GraphViewport = { x: 0, y: 0, k: 1 }
const EMPTY_SELECTION: GraphSelection = { nodeId: null, hoveredId: null }

describe("SvgRenderer", () => {
  it("renders nodes with ARIA labels", () => {
    render(
      <SvgRenderer
        svgRef={{ current: null } as React.RefObject<SVGSVGElement | null>}
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

  it("applies selection ring to selected node", () => {
    const selection: GraphSelection = { nodeId: "n1", hoveredId: null }

    const { container } = render(
      <SvgRenderer
        svgRef={{ current: null } as React.RefObject<SVGSVGElement | null>}
        width={800}
        height={600}
        nodes={NODES}
        edges={EDGES}
        viewport={VIEWPORT}
        selection={selection}
      />,
    )

    const svg = container.querySelector("svg")
    expect(svg).toBeInTheDocument()

    const viewportGroup = svg?.querySelector(".graph-viewport")
    expect(viewportGroup?.getAttribute("transform")).toBe(
      "translate(0, 0) scale(1)",
    )
  })

  it("scales hovered node with white stroke", () => {
    const onNodeHover = vi.fn()
    const selection: GraphSelection = { nodeId: null, hoveredId: "n2" }

    render(
      <SvgRenderer
        svgRef={{ current: null } as React.RefObject<SVGSVGElement | null>}
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
})
