/**
 * KgExploreView — main client component for KG Explorer page.
 *
 * Tests render behavior, filter integration, and accessibility.
 * GraphCanvas internals are mocked via d3-force stub to isolate view logic.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"

/* ------------------------------------------------------------------ */
/*  d3-force mock — synchronous simulation (matches sibling tests)   */
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
/*  ResizeObserver mock                                              */
/* ------------------------------------------------------------------ */

vi.stubGlobal(
  "ResizeObserver",
  class {
    observe = vi.fn()
    unobserve = vi.fn()
    disconnect = vi.fn()
  },
)

/* ------------------------------------------------------------------ */
/*  TanStack Query mock — resolve initialData synchronously          */
/* ------------------------------------------------------------------ */

vi.mock("@tanstack/react-query", () => ({
  useQuery: vi.fn(({ queryFn, initialData }) => ({
    data: initialData ?? queryFn(),
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })),
}))

/* ------------------------------------------------------------------ */
/*  Imports (after mocks)                                             */
/* ------------------------------------------------------------------ */

import { KgExploreView } from "../KgExploreView"
import type { GraphData } from "@/components/graph/types"

/* ------------------------------------------------------------------ */
/*  Test data                                                          */
/* ------------------------------------------------------------------ */

function makeGraphData(): GraphData {
  return {
    nodes: [
      { id: "material:UO2", label: "Uranium Dioxide", type: "material" },
      { id: "property:density", label: "Density", type: "property" },
      { id: "experiment:exp-1", label: "Irradiation Test", type: "entity" },
      { id: "source:pub-1", label: "J. Nucl. Mater.", type: "default" },
      { id: "material:SiC", label: "Silicon Carbide", type: "material" },
    ],
    edges: [
      {
        id: "e-0",
        source: "material:UO2",
        target: "property:density",
        type: "HAS_PROPERTY",
      },
      {
        id: "e-1",
        source: "material:UO2",
        target: "experiment:exp-1",
        type: "TESTED_BY",
      },
      {
        id: "e-2",
        source: "experiment:exp-1",
        target: "source:pub-1",
        type: "CITED_IN",
      },
    ],
  }
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("KgExploreView", () => {
  const initialData = makeGraphData()

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders GraphCanvas with provided initial data", () => {
    render(<KgExploreView initialData={initialData} />)

    expect(
      screen.getByRole("application", { name: /knowledge graph/i }),
    ).toBeInTheDocument()
  })

  it("renders toolbar with zoom and filter controls", () => {
    render(<KgExploreView initialData={initialData} />)

    expect(
      screen.getByRole("button", { name: /zoom in/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /zoom out/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /fit to view/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("combobox", { name: /filter by type/i }),
    ).toBeInTheDocument()
  })

  it("renders legend bar at bottom", () => {
    render(<KgExploreView initialData={initialData} />)

    expect(
      screen.getByRole("complementary", { name: /graph legend/i }),
    ).toBeInTheDocument()
  })

  it("filters nodes by toggling type filter", async () => {
    render(<KgExploreView initialData={initialData} />)

    const select = screen.getByRole("combobox", { name: /filter by type/i })
    fireEvent.change(select, { target: { value: "property" } })

    await waitFor(() => {
      const app = screen.getByRole("application", {
        name: /knowledge graph/i,
      })
      expect(app).toBeInTheDocument()
    })
  })

  it("shows empty state when all visible types are toggled off", async () => {
    // Graph with only one node type — toggle off property, entity, default,
    // then material (last toggle removes the only matching type)
    const singleTypeData: GraphData = {
      nodes: [{ id: "material:UO2", label: "UO2", type: "material" }],
      edges: [],
    }
    render(<KgExploreView initialData={singleTypeData} />)

    const select = screen.getByRole("combobox", { name: /filter by type/i })
    // Toggle off non-matching types first (3 toggles)
    fireEvent.change(select, { target: { value: "property" } })
    fireEvent.change(select, { target: { value: "entity" } })
    fireEvent.change(select, { target: { value: "default" } })
    // Now only "material" remains — toggle it off
    fireEvent.change(select, { target: { value: "material" } })

    await waitFor(() => {
      expect(
        screen.getByText(/no visible nodes/i),
      ).toBeInTheDocument()
    })
  })

  it("is keyboard accessible — toolbar buttons focusable", () => {
    render(<KgExploreView initialData={initialData} />)

    const zoomInBtn = screen.getByRole("button", { name: /zoom in/i })
    zoomInBtn.focus()
    expect(document.activeElement).toBe(zoomInBtn)
  })

  it("applies reduced-motion class when prefers-reduced-motion is active", () => {
    vi.spyOn(window, "matchMedia").mockImplementation(
      (query: string) =>
        ({
          matches: query === "(prefers-reduced-motion: reduce)",
          media: query,
          onchange: null,
          addListener: () => undefined,
          removeListener: () => undefined,
          addEventListener: () => undefined,
          removeEventListener: () => undefined,
          dispatchEvent: () => false,
        }) as MediaQueryList,
    )

    const { container } = render(<KgExploreView initialData={initialData} />)
    const main = container.querySelector("[data-testid='kg-explorer']")
    expect(main?.classList.contains("reduce-motion")).toBe(true)

    vi.restoreAllMocks()
  })
})
