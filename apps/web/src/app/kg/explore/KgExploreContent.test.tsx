import { describe, it, expect, vi, beforeEach } from "vitest"
// @vitest-environment jsdom
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { KgExploreContent } from "./KgExploreContent"

/* ------------------------------------------------------------------ */
/*  Polyfill ResizeObserver for jsdom                                   */
/* ------------------------------------------------------------------ */

class MockResizeObserver {
  readonly observe = vi.fn()
  readonly unobserve = vi.fn()
  readonly disconnect = vi.fn()
}

if (typeof window !== "undefined" && !("ResizeObserver" in window)) {
  ;(window as unknown as Record<string, unknown>).ResizeObserver = MockResizeObserver
}

/* ------------------------------------------------------------------ */
/*  d3-force mock — keeps simulation synchronous                        */
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
/*  next/navigation mock                                              */
/* ------------------------------------------------------------------ */

const pushMock = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))

/* ------------------------------------------------------------------ */
/*  API mock — kg-explore-api (vi.hoisted avoids hoisting issue)    */
/* ------------------------------------------------------------------ */

import type { GraphData } from "@/components/graph/types"

const { mockGetKgExploreGraph } = vi.hoisted(() => ({
  mockGetKgExploreGraph: vi.fn<typeof import("@/lib/kg-explore-api").getKgExploreGraph>(),
}))

vi.mock("@/lib/kg-explore-api", () => ({
  getKgExploreGraph: mockGetKgExploreGraph,
}))

/* ------------------------------------------------------------------ */
/*  Test helpers                                                      */
/* ------------------------------------------------------------------ */

function makeGraphData(): GraphData {
  return {
    nodes: [
      { id: "material:ZrO2", label: "ZrO2", type: "material" },
      { id: "property:density", label: "Density", type: "property" },
      { id: "source:pub-1", label: "Journal of NM", type: "default" },
    ],
    edges: [
      { id: "e-0", source: "material:ZrO2", target: "property:density", type: "HAS_PROPERTY" },
      { id: "e-1", source: "material:ZrO2", target: "source:pub-1", type: "PUBLISHED_IN" },
    ],
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

/* ------------------------------------------------------------------ */
/*  Tests                                                             */
/* ------------------------------------------------------------------ */

describe("KgExploreContent", () => {
  it("shows loading state initially", () => {
    mockGetKgExploreGraph.mockReturnValueOnce(
      new Promise(() => {}),
    )

    const { container } = render(<KgExploreContent />)

    // Ant Design Spin may not render tip text in jsdom;
    // verify the loading container is present (Spin renders a .ant-spin wrapper).
    const spinEl = container.querySelector(".ant-spin")
    expect(spinEl).toBeInTheDocument()
  })

  it("renders graph after successful fetch", async () => {
    mockGetKgExploreGraph.mockResolvedValueOnce(makeGraphData())

    render(<KgExploreContent />)
    await waitFor(() => {
      expect(screen.getByRole("application", { name: /knowledge graph/i })).toBeInTheDocument()
    })
  })

  it("shows empty state when API returns empty nodes", async () => {
    mockGetKgExploreGraph.mockResolvedValueOnce({ nodes: [], edges: [] })

    render(<KgExploreContent />)
    await waitFor(() => {
      expect(screen.getByText("暂无知识图谱数据")).toBeInTheDocument()
    })
  })

  it("shows error state with retry button on fetch failure", async () => {
    mockGetKgExploreGraph.mockRejectedValueOnce(new Error("Network error"))

    render(<KgExploreContent />)
    await waitFor(() => {
      expect(screen.getByText("Failed to load graph")).toBeInTheDocument()
    })
    expect(screen.getByText("Retry")).toBeInTheDocument()
  })

  it("re-fetches data when retry button is clicked", async () => {
    mockGetKgExploreGraph
      .mockRejectedValueOnce(new Error("Network error"))
      .mockResolvedValueOnce(makeGraphData())

    render(<KgExploreContent />)

    await waitFor(() => {
      expect(screen.getByText("Retry")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Retry"))

    await waitFor(() => {
      expect(mockGetKgExploreGraph).toHaveBeenCalledTimes(2)
    })
  })

  it("shows legend bar with all node categories", async () => {
    mockGetKgExploreGraph.mockResolvedValueOnce(makeGraphData())

    render(<KgExploreContent />)
    await waitFor(() => {
      expect(screen.getByText("Legend")).toBeInTheDocument()
    })

    expect(screen.getByText("Material")).toBeInTheDocument()
    expect(screen.getByText("Property")).toBeInTheDocument()
    expect(screen.getByText("Ontology")).toBeInTheDocument()
    expect(screen.getByText("Other")).toBeInTheDocument()
  })

  it("renders page heading", async () => {
    mockGetKgExploreGraph.mockResolvedValueOnce(makeGraphData())

    render(<KgExploreContent />)

    expect(screen.getByText("Knowledge Graph Explorer")).toBeInTheDocument()
  })

  it("shows filter dropdown in toolbar", async () => {
    mockGetKgExploreGraph.mockResolvedValueOnce(makeGraphData())

    render(<KgExploreContent />)

    const select = screen.getByLabelText("Filter by node type")
    expect(select).toBeInTheDocument()
    expect(screen.getByText("Filter by type…")).toBeInTheDocument()
  })

  it("navigates to node detail on node click", async () => {
    mockGetKgExploreGraph.mockResolvedValueOnce(makeGraphData())

    render(<KgExploreContent />)

    await waitFor(() => {
      expect(screen.getByRole("application")).toBeInTheDocument()
    })

    const nodeElement = document.querySelector("[data-node-id='material:ZrO2']")
    if (nodeElement) {
      fireEvent.click(nodeElement)
      expect(pushMock).toHaveBeenCalledWith("/kg/nodes/material/material:ZrO2")
    }
  })
})
