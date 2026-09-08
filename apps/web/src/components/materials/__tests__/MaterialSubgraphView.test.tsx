import { describe, it, expect, vi, beforeEach } from "vitest"
// @vitest-environment jsdom
// eslint-disable-next-line @typescript-eslint/no-var-requires
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MaterialSubgraphView } from "../MaterialSubgraphView"
import { ApiError } from "@/lib/api-client"
import type { ReactNode } from "react"

/**
 * NFM-4449: MaterialSubgraphView now reads its data via useGraphView,
 * which wraps `useQuery` from TanStack Query. Every render in the
 * non-skipped suites below must therefore be wrapped in a fresh
 * QueryClientProvider. We keep `retry: false` so a thrown fetch
 * doesn't retry under the hood and confuse the per-test mock-call
 * counts.
 */
function renderWithQueryClient(node: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={queryClient}>{node}</QueryClientProvider>,
  )
}

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
import type { GraphData, GraphNode, GraphEdge } from "@/components/graph/types"

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
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockResolvedValue(makeGraphData())
  })

  it("renders loading state initially", () => {
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}))

    const { container } = renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)

    // Ant Design Spin renders with aria-busy but the tip text may not
    // be directly queryable by getByText in jsdom.
    expect(container.querySelector("[aria-busy]")).toBeTruthy()
  })

  it("fetches subgraph via getMaterialSubgraph on mount", async () => {
    renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(getMaterialSubgraph).toHaveBeenCalledWith("ZrO2", 2)
    })
  })

  it("renders GraphCanvas after data loads", async () => {
    renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(
        screen.getByRole("application", {
          name: /interactive knowledge graph/i,
        }),
      ).toBeInTheDocument()
    })
  })

  it("applies aria-label from focal material label on the wrapper", async () => {
    renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(
        screen.getByLabelText(/Material knowledge graph for Zirconium Dioxide/i),
      ).toBeInTheDocument()
    })
  })

  it("navigates to /materials/<id> when clicking a material node, stripping prefix", async () => {
    renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Node: Silicon Carbide/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: Silicon Carbide/i }))

    expect(pushMock).toHaveBeenCalledWith("/materials/SiC")
  })

  it("does not navigate when clicking a non-material node — shows tooltip instead", async () => {
    renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Node: Density/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: Density/i }))

    expect(pushMock).not.toHaveBeenCalled()
    expect(screen.getByRole("tooltip")).toBeInTheDocument()
  })

  it("renders error state with retry button when fetch rejects", async () => {
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("Network down"))

    renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(screen.getByText(/network down/i)).toBeInTheDocument()
    })

    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
  })

  it("refetches when retry is clicked after error", async () => {
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce(new Error("Network down"))
      .mockResolvedValueOnce(makeGraphData())

    renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)

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

    renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(screen.getByText(/暂无关联节点|no related nodes/i)).toBeInTheDocument()
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
    return renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)
  }

  it("strips material: prefix and navigates for material-type node", async () => {
    renderWithNodes([{ id: "material:ZrO2", label: "Zirconium Dioxide", type: "material" }])

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Node: Zirconium Dioxide/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: Zirconium Dioxide/i }))

    expect(pushMock).toHaveBeenCalledWith("/materials/ZrO2")
  })

  it("navigates bare-id material node without prefix", async () => {
    renderWithNodes([{ id: "UO2", label: "Uranium Dioxide", type: "material" }])

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Node: Uranium Dioxide/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: Uranium Dioxide/i }))

    expect(pushMock).toHaveBeenCalledWith("/materials/UO2")
  })

  it("shows tooltip for property node (no navigation)", async () => {
    renderWithNodes(
      [
        { id: "material:ZrO2", label: "ZrO2", type: "material" },
        { id: "property:density", label: "Density", type: "property" },
      ],
      [{ id: "e-0", source: "material:ZrO2", target: "property:density", type: "X" }],
    )

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Node: Density/i })).toBeInTheDocument()
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
      expect(screen.getByRole("button", { name: /Node: Thermal Test/i })).toBeInTheDocument()
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
      expect(screen.getByRole("button", { name: /Node: Journal/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: Journal/i }))

    expect(pushMock).not.toHaveBeenCalled()
  })
})

/* ------------------------------------------------------------------ */
/*  NFM-4096 — coverage-gap empty-state banner (NFM-4093 link)        */
/*                                                                     */
/*  When /kg/graph/subgraph returns 404 for a material without a       */
/*  Material kg_node bridge, the component must surface the new        */
/*  empty-state banner (mentioning NFM-4093 + linking to it) instead  */
/*  of the generic error Alert. The 5xx path must keep the Alert.     */
/* ------------------------------------------------------------------ */

describe("MaterialSubgraphView — NFM-4096 coverage-gap banner", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders the NFM-4093 banner copy and link on a 404 from the API", async () => {
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError("Not Found", 404),
    )

    renderWithQueryClient(<MaterialSubgraphView materialId="U-10Mo" />)

    // The banner copy explicitly references NFM-4093
    await waitFor(() => {
      expect(screen.getByText(/No knowledge-graph data yet for this material/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/NFM-4093/)).toBeInTheDocument()

    // And links to the parent issue using the project's deep-link convention
    const link = screen.getByRole("link", { name: /NFM-4093/i })
    expect(link).toHaveAttribute("href", "/NFM/issues/NFM-4093")
  })

  it("does NOT show the generic error Alert on 404 — coverage gap is not an error", async () => {
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError("Not Found", 404),
    )

    renderWithQueryClient(<MaterialSubgraphView materialId="U-10Mo" />)

    await waitFor(() => {
      expect(screen.getByText(/NFM-4093/)).toBeInTheDocument()
    })

    // The generic-error Alert must not be visible on 404.
    expect(screen.queryByText(/加载知识图谱失败/i)).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument()
  })

  it("still shows the generic error Alert on 5xx — NFM-4096 must not mask real errors", async () => {
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError("Internal Server Error", 500),
    )

    renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(screen.getByText(/Internal Server Error/i)).toBeInTheDocument()
    })

    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
    expect(screen.queryByText(/NFM-4093/)).not.toBeInTheDocument()
  })

  it("still shows the generic error Alert on network failure (non-ApiError)", async () => {
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("Network down"))

    renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(screen.getByText(/network down/i)).toBeInTheDocument()
    })

    expect(screen.queryByText(/NFM-4093/)).not.toBeInTheDocument()
  })

  it("renders the NFM-4093 banner when the API returns an empty graph (zero nodes)", async () => {
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockResolvedValue({
      nodes: [],
      edges: [],
    })

    renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)

    await waitFor(() => {
      expect(screen.getByText(/NFM-4093/)).toBeInTheDocument()
    })

    const link = screen.getByRole("link", { name: /NFM-4093/i })
    expect(link).toHaveAttribute("href", "/NFM/issues/NFM-4093")
  })
})

/* ------------------------------------------------------------------ */
/*  NFM-4445 — KG-node vs materials.id bridge in click routing         */
/*                                                                     */
/*  The KG-node UUID (e.g. ``496cf283-…``) is independent from the     */
/*  canonical materials row (e.g. ``068dc946-…`` for UO2).  The API     */
/*  must surface the bridge on every Material node via ``materials_id``*/
/*  and the click handler must route to ``/materials/{materials_id}``,  */
/*  never to ``/materials/{node.id}``.  Same-name cohorts without a
/*  bridge (NFM-4093) must show tooltip-only.                          */
/* ------------------------------------------------------------------ */

const KG_UO2_UUID = "496cf283-0000-0000-0000-000000000001"
const MAT_UO2_UUID = "068dc946-0000-0000-0000-000000000001"
const KG_SIC_UUID = "496cf283-0000-0000-0000-000000000002"
const MAT_SIC_UUID = "068dc946-0000-0000-0000-000000000002"
const KG_DUP_UUID = "496cf283-0000-0000-0000-000000000003"
// Cr-doped UO2 same-name cohort (NFM-4093) — bridge intentionally absent.

describe("MaterialSubgraphView — NFM-4445 materials_id bridge routing", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  function renderWithGraph(nodes: GraphNode[], edges: GraphEdge[] = []) {
    const data: GraphData = { nodes, edges }
    ;(getMaterialSubgraph as ReturnType<typeof vi.fn>).mockResolvedValue(data)
    return renderWithQueryClient(<MaterialSubgraphView materialId="ZrO2" />)
  }

  it("routes to /materials/{materials_id} when the bridge is supplied", async () => {
    // The KG-node UUID (496cf283-…) must NOT be used as the URL id — only
    // the server-supplied materials_id bridge (068dc946-…) is the source
    // of truth.
    renderWithGraph([
      {
        id: KG_UO2_UUID,
        label: "UO2",
        type: "material",
        materials_id: MAT_UO2_UUID,
      },
    ])

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Node: UO2/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: UO2/i }))

    expect(pushMock).toHaveBeenCalledWith(`/materials/${MAT_UO2_UUID}`)
    // Specifically: NOT the KG UUID.
    expect(pushMock).not.toHaveBeenCalledWith(`/materials/${KG_UO2_UUID}`)
  })

  it("routes neighbour Material nodes via their own bridge", async () => {
    renderWithGraph(
      [
        {
          id: KG_UO2_UUID,
          label: "UO2",
          type: "material",
          materials_id: MAT_UO2_UUID,
        },
        {
          id: KG_SIC_UUID,
          label: "SiC",
          type: "material",
          materials_id: MAT_SIC_UUID,
        },
      ],
      [{ id: "e-0", source: KG_UO2_UUID, target: KG_SIC_UUID, type: "RELATED_TO" }],
    )

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Node: SiC/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: SiC/i }))

    expect(pushMock).toHaveBeenCalledWith(`/materials/${MAT_SIC_UUID}`)
    expect(pushMock).not.toHaveBeenCalledWith(`/materials/${KG_SIC_UUID}`)
  })

  it("shows tooltip-only when Material node lacks a bridge (NFM-4093 cohort)", async () => {
    // Same-name duplicate cohort (Cr-doped UO2): KG-node exists but
    // server returned materials_id=null.  Frontend must NOT navigate
    // (which would silently mis-route to one of the duplicates).
    renderWithGraph([
      {
        id: KG_DUP_UUID,
        label: "Cr-doped UO2",
        type: "material",
        materials_id: undefined,
      },
    ])

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Node: Cr-doped UO2/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: Cr-doped UO2/i }))

    expect(pushMock).not.toHaveBeenCalled()
    expect(screen.getByRole("tooltip")).toBeInTheDocument()
  })

  it("shows tooltip-only when Material node has no materials_id field at all", async () => {
    // Defensive: even if the server omits the field entirely, no nav.
    renderWithGraph([
      {
        id: KG_UO2_UUID,
        label: "UO2",
        type: "material",
        // materials_id intentionally undefined (older server response shape)
      },
    ])

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Node: UO2/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: UO2/i }))

    expect(pushMock).not.toHaveBeenCalled()
    expect(screen.getByRole("tooltip")).toBeInTheDocument()
  })

  it("non-material nodes still show tooltip-only (regression)", async () => {
    renderWithGraph(
      [
        {
          id: KG_UO2_UUID,
          label: "UO2",
          type: "material",
          materials_id: MAT_UO2_UUID,
        },
        {
          id: "property:density",
          label: "Density",
          type: "property",
        },
      ],
      [{ id: "e-0", source: KG_UO2_UUID, target: "property:density", type: "HAS_PROPERTY" }],
    )

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Node: Density/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Node: Density/i }))

    expect(pushMock).not.toHaveBeenCalled()
    expect(screen.getByRole("tooltip")).toBeInTheDocument()
  })
})
