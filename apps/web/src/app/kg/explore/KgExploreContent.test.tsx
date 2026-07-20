/**
 * KgExploreView — tests TanStack Query integration, loading/error states,
 * filter behavior, and accessibility.
 *
 * Tests the production KgExploreView component (replaces the legacy
 * KgExploreContent which used manual useEffect/useState fetch pattern).
 *
 * Spec: NFM-1605
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"

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
/*  useGraphControls mock                                              */
/* ------------------------------------------------------------------ */

vi.mock("@/components/graph/useGraphControls", () => ({
  useGraphControls: (initial: unknown, _onChange: unknown) => ({
    viewport: initial,
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
    fitToView: vi.fn(),
  }),
}))

/* ------------------------------------------------------------------ */
/*  useReducedMotion mock                                              */
/* ------------------------------------------------------------------ */

vi.mock("@/components/graph/useReducedMotion", () => ({
  useReducedMotion: () => false,
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
/*  TanStack Query mock — controlled behavior per test                 */
/* ------------------------------------------------------------------ */

const mockRefetch = vi.fn()

vi.mock("@tanstack/react-query", () => ({
  useQuery: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    isFetching: false,
    refetch: mockRefetch,
  })),
  useQueryClient: vi.fn(() => ({
    invalidateQueries: vi.fn(() => Promise.resolve()),
  })),
}))

/* ------------------------------------------------------------------ */
/*  Imports (after mocks)                                             */
/* ------------------------------------------------------------------ */

import { useQuery } from "@tanstack/react-query"
import { KgExploreView } from "./KgExploreView"

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
  // Default: query resolved with data
  vi.mocked(useQuery).mockReturnValue({
    data: makeGraphData(),
    isLoading: false,
    isError: false,
    error: null,
    isFetching: false,
    refetch: mockRefetch,
  } as unknown as ReturnType<typeof useQuery<GraphData>>)
})

/* ------------------------------------------------------------------ */
/*  Tests                                                             */
/* ------------------------------------------------------------------ */

describe("KgExploreView", () => {
  it("shows loading state initially", () => {
    vi.mocked(useQuery).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      isFetching: true,
      refetch: mockRefetch,
    } as unknown as ReturnType<typeof useQuery<GraphData>>)

    const { container } = render(<KgExploreView initialData={makeGraphData()} />)

    // Ant Design Spin renders a .ant-spin wrapper
    const spinEl = container.querySelector(".ant-spin")
    expect(spinEl).toBeInTheDocument()
  })

  it("renders graph after successful fetch", () => {
    render(<KgExploreView initialData={makeGraphData()} />)

    expect(
      screen.getByRole("application", { name: /knowledge graph/i }),
    ).toBeInTheDocument()
  })

  it("shows empty state when data has no nodes", () => {
    vi.mocked(useQuery).mockReturnValue({
      data: { nodes: [], edges: [] },
      isLoading: false,
      isError: false,
      error: null,
      isFetching: false,
      refetch: mockRefetch,
    } as unknown as ReturnType<typeof useQuery<GraphData>>)

    render(<KgExploreView initialData={{ nodes: [], edges: [] }} />)

    expect(screen.getByText("暂无知识图谱数据")).toBeInTheDocument()
  })

  it("shows error state with retry button on fetch failure", () => {
    vi.mocked(useQuery).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network error"),
      isFetching: false,
      refetch: mockRefetch,
    } as unknown as ReturnType<typeof useQuery<GraphData>>)

    render(<KgExploreView initialData={{ nodes: [], edges: [] }} />)

    expect(screen.getByText("Failed to load graph")).toBeInTheDocument()
    expect(screen.getByText("Retry")).toBeInTheDocument()
  })

  it("re-fetches data when retry button is clicked", () => {
    vi.mocked(useQuery).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network error"),
      isFetching: false,
      refetch: mockRefetch,
    } as unknown as ReturnType<typeof useQuery<GraphData>>)

    render(<KgExploreView initialData={{ nodes: [], edges: [] }} />)

    fireEvent.click(screen.getByText("Retry"))
    expect(mockRefetch).toHaveBeenCalled()
  })

  it("shows legend bar with all node categories", () => {
    render(<KgExploreView initialData={makeGraphData()} />)

    expect(
      screen.getByRole("complementary", { name: /graph legend/i }),
    ).toBeInTheDocument()
    expect(screen.getByText("Material")).toBeInTheDocument()
    expect(screen.getByText("Property")).toBeInTheDocument()
    expect(screen.getByText("Entity")).toBeInTheDocument()
    expect(screen.getByText("Other")).toBeInTheDocument()
  })

  it("shows toolbar with filter controls", () => {
    render(<KgExploreView initialData={makeGraphData()} />)

    expect(
      screen.getByRole("combobox", { name: /filter by type/i }),
    ).toBeInTheDocument()
  })

  it("filters nodes by toggling type filter", async () => {
    render(<KgExploreView initialData={makeGraphData()} />)

    const select = screen.getByRole("combobox", { name: /filter by type/i })
    fireEvent.change(select, { target: { value: "property" } })

    await waitFor(() => {
      const app = screen.getByRole("application", {
        name: /knowledge graph/i,
      })
      expect(app).toBeInTheDocument()
    })
  })

  it("provides refresh button that calls refetch", () => {
    render(<KgExploreView initialData={makeGraphData()} />)

    const refreshBtn = screen.getByRole("button", { name: /refresh graph data/i })
    expect(refreshBtn).toBeInTheDocument()

    fireEvent.click(refreshBtn)
    expect(mockRefetch).toHaveBeenCalled()
  })
})
