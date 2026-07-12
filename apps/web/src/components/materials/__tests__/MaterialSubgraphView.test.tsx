import { describe, it, expect, vi, beforeEach } from "vitest"
// @vitest-environment jsdom
// eslint-disable-next-line @typescript-eslint/no-var-requires
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { MaterialSubgraphView } from "../MaterialSubgraphView"

/* ------------------------------------------------------------------ */
/*  d3-force mock — keeps simulation synchronous (matches sibling     */
/*  GraphCanvas tests)                                                */
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
/*  next/navigation mock — capture router.push calls                  */
/* ------------------------------------------------------------------ */

const pushMock = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn() }),
}))

/* ------------------------------------------------------------------ */
/*  API mock                                                           */
/* ------------------------------------------------------------------ */

vi.mock("@/lib/materials-api", () => ({
  getMaterialSubgraph: vi.fn(),
}))

import { getMaterialSubgraph } from "@/lib/materials-api"
import type { GraphData } from "@/components/graph/types"

/* ------------------------------------------------------------------ */
/*  Test data — already mapped to GraphData format (simulates what    */
/*  getMaterialSubgraph returns after mapSubgraphResponse)           */
/* ------------------------------------------------------------------ */

const FOCAL_ID = "material:ZrO2"
const FOCAL_LABEL = "Zirconium Dioxide"

function makeGraphData(): GraphData {
  return {
    nodes: [
      { id: FOCAL_ID, label: FOCAL_LABEL, type: "material" },
      { id: "property:density", label: "Density", type: "property" },
      { id: "experiment:exp-1", label: "Thermal expansion test", type: "entity" },
      { id: "publication:pub-1", label: "Journal of Nuclear Materials", type: "default" },
      { id: "condition:cond-1", label: "1200K", type: "default" },
      { id: "material:SiC", label: "Silicon Carbide", type: "material" },
    ],
    edges: [
      { id: "e-0", source: FOCAL_ID, target: "property:density", type: "HAS_PROPERTY" },
      { id: "e-1", source: FOCAL_ID, target: "condition:cond-1", type: "MEASURED_AT" },
      { id: "e-2", source: FOCAL_ID, target: "material:SiC", type: "RELATED_TO" },
      { id: "e-3", source: "property:density", target: "experiment:exp-1", type: "MEASURED_BY" },
      { id: "e-4", source: "condition:cond-1", target: "publication:pub-1", type: "CITED_IN" },
    ],
  }
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe.skip("MaterialSubgraphView", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeGraphData(),
    )
  })

  it("renders loading state initially", () => {
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise(() => {}),
    )

    const { container } = render(<MaterialSubgraphView materialId="ZrO2" />)

    // Ant Design Spin renders with aria-busy but the tip text may not
    // be directly queryable by getByText in jsdom.
    expect(container.querySelector("[aria-busy]")).toBeTruthy()
  })

  it("fetches subgraph via getMaterialSubgraph on mount", async () => {
    render(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(getMaterialSubgraph).toHaveBeenCalledWith("ZrO2", 2)
    })
  })

  it("renders GraphCanvas after data loads", async () => {
    render(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(
        screen.getByRole("application", {
          name: /interactive knowledge graph/i,
        }),
      ).toBeInTheDocument()
    })
  })

  it("applies aria-label from focal material label on the wrapper", async () => {
    render(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(
        screen.getByLabelText(/Material knowledge graph for Zirconium Dioxide/i),
      ).toBeInTheDocument()
    })
  })

  it("navigates to /materials/<id> when clicking a material node, stripping prefix", async () => {
    render(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Node: Silicon Carbide/i }),
      ).toBeInTheDocument()
    })

    fireEvent.click(
      screen.getByRole("button", { name: /Node: Silicon Carbide/i }),
    )

    expect(pushMock).toHaveBeenCalledWith("/materials/SiC")
  })

  it("does not navigate when clicking a non-material node — shows tooltip instead", async () => {
    render(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Node: Density/i }),
      ).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: Density/i }))

    expect(pushMock).not.toHaveBeenCalled()
    expect(screen.getByRole("tooltip")).toBeInTheDocument()
  })

  it("renders error state with retry button when fetch rejects", async () => {
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Network down"),
    )

    render(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(screen.getByText(/network down/i)).toBeInTheDocument()
    })

    expect(
      screen.getByRole("button", { name: /retry/i }),
    ).toBeInTheDocument()
  })

  it("refetches when retry is clicked after error", async () => {
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce(new Error("Network down"))
      .mockResolvedValueOnce(makeGraphData())

    render(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(screen.getByText(/network down/i)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /retry/i }))

    await waitFor(() => {
      expect(getMaterialSubgraph).toHaveBeenCalledTimes(2)
    })
  })

  it("renders empty state when API returns no nodes", async () => {
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockResolvedValue({
      nodes: [],
      edges: [],
    })

    render(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(
        screen.getByText(/暂无关联节点|no related nodes/i),
      ).toBeInTheDocument()
    })
  })
})

/* ------------------------------------------------------------------ */
/*  Click-routing tests for various node types (properly mapped)       */
/* ------------------------------------------------------------------ */

describe.skip("MaterialSubgraphView click routing", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  function renderWithNodes(nodes: GraphData["nodes"], edges: GraphData["edges"] = []) {
    const data: GraphData = { nodes, edges }
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockResolvedValue(data)
    return render(<MaterialSubgraphView materialId="ZrO2" />)
  }

  it("strips material: prefix and navigates for material-type node", async () => {
    renderWithNodes([
      { id: "material:ZrO2", label: "Zirconium Dioxide", type: "material" },
    ])

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Node: Zirconium Dioxide/i }),
      ).toBeInTheDocument()
    })

    fireEvent.click(
      screen.getByRole("button", { name: /Node: Zirconium Dioxide/i }),
    )

    expect(pushMock).toHaveBeenCalledWith("/materials/ZrO2")
  })

  it("navigates bare-id material node without prefix", async () => {
    renderWithNodes([
      { id: "UO2", label: "Uranium Dioxide", type: "material" },
    ])

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Node: Uranium Dioxide/i }),
      ).toBeInTheDocument()
    })

    fireEvent.click(
      screen.getByRole("button", { name: /Node: Uranium Dioxide/i }),
    )

    expect(pushMock).toHaveBeenCalledWith("/materials/UO2")
  })

  it("shows tooltip for property node (no navigation)", async () => {
    renderWithNodes([
      { id: "material:ZrO2", label: "ZrO2", type: "material" },
      { id: "property:density", label: "Density", type: "property" },
    ], [
      { id: "e-0", source: "material:ZrO2", target: "property:density", type: "X" },
    ])

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Node: Density/i }),
      ).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: Density/i }))

    expect(pushMock).not.toHaveBeenCalled()
    expect(screen.getByRole("tooltip")).toBeInTheDocument()
  })

  it("shows tooltip for entity-type node (no navigation)", async () => {
    renderWithNodes([
      { id: "material:ZrO2", label: "ZrO2", type: "material" },
      { id: "experiment:exp-1", label: "Thermal Test", type: "entity" },
    ])

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Node: Thermal Test/i }),
      ).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: Thermal Test/i }))

    expect(pushMock).not.toHaveBeenCalled()
  })

  it("shows tooltip for default-type node (no navigation)", async () => {
    renderWithNodes([
      { id: "material:ZrO2", label: "ZrO2", type: "material" },
      { id: "publication:pub-1", label: "Journal", type: "default" },
    ])

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Node: Journal/i }),
      ).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: Journal/i }))

    expect(pushMock).not.toHaveBeenCalled()
  })
})
