import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

// ── Mock next/navigation ──────────────────────────────────────────────

const mockPush = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
  })),
}))

// ── Mock next/link ────────────────────────────────────────────────────

vi.mock("next/link", () => {
  function MockLink({
    children,
    href,
    ...props
  }: {
    children: React.ReactNode
    href: string
    [key: string]: unknown
  }) {
    return <a href={href} {...props}>{children}</a>
  }
  MockLink.displayName = "MockLink"
  return MockLink
})

// ── Mock next/dynamic to return a stub GraphCanvas ──────────────────────

vi.mock("next/dynamic", () => ({
  __esModule: true,
  default: (loader: () => Promise<{ default: unknown }>) =>
    loader().then((mod) => mod),
}))

// ── Mock d3-force (required by GraphCanvas) ───────────────────────────

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

// ── Mock kg-api ────────────────────────────────────────────────────────

const MOCK_FOCAL_ID = "uuid-focal-123"
const MOCK_RESPONSE = {
  focal: { id: MOCK_FOCAL_ID, depth: 0 },
  nodes: [
    {
      id: MOCK_FOCAL_ID,
      label: "Uranium Dioxide",
      type: "material",
      properties: { __depth: 0 },
      status: "active",
      confidence: 0.95,
      source_id: "src-1",
    },
    {
      id: "uuid-prop-456",
      label: "Melting Point",
      type: "property",
      properties: { __depth: 1 },
      status: "active",
      confidence: 0.9,
      source_id: null,
    },
    {
      id: "uuid-method-789",
      label: "DFT Calculation",
      type: "method",
      properties: { __depth: 2 },
      status: "active",
      confidence: 0.85,
      source_id: "src-2",
    },
  ],
  edges: [
    {
      source: MOCK_FOCAL_ID,
      target: "uuid-prop-456",
      type: "has_property",
      properties: {},
      confidence: 0.9,
    },
    {
      source: "uuid-prop-456",
      target: "uuid-method-789",
      type: "measured_by",
      properties: {},
      confidence: 0.85,
    },
  ],
}

const mockGetKGGraph = vi.fn()
vi.mock("@/lib/kg-api", () => ({
  getKGGraph: (...args: unknown[]) => mockGetKGGraph(...args),
  transformGraphResponse: vi.fn((resp: typeof MOCK_RESPONSE) => ({
    nodes: resp.nodes.map((n) => ({
      id: n.id,
      label: n.label,
      type: n.type === "material" ? "material" as const
        : n.type === "property" ? "property" as const
        : "entity" as const,
      size: n.id === resp.focal.id ? 20 : 10,
      color: n.id === resp.focal.id ? "#f59e0b" : undefined,
    })),
    edges: resp.edges.map((e, i) => ({
      id: `e-${i}`,
      source: e.source,
      target: e.target,
      type: e.type,
    })),
  })),
}))

// ── Import after mocks ──────────────────────────────────────────────────

import { MaterialGraphView } from "../MaterialGraphView"

// ── Helper ──────────────────────────────────────────────────────────────

function renderView(materialId: string) {
  return render(
    <MemoryRouter>
      <MaterialGraphView materialId={materialId} />
    </MemoryRouter>,
  )
}

// ── Tests ──────────────────────────────────────────────────────────────

describe("MaterialGraphView", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetKGGraph.mockResolvedValue(MOCK_RESPONSE)
  })

  it("shows loading skeleton while fetching", async () => {
    mockGetKGGraph.mockReturnValue(new Promise(() => {})) // never resolves
    renderView("test-material")

    expect(screen.getByRole("status")).toBeInTheDocument()
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true")
  })

  it("renders graph after successful fetch", async () => {
    renderView("test-material")

    await waitFor(() => {
      expect(
        screen.getByRole("application", { name: /knowledge graph/i }),
      ).toBeInTheDocument()
    })
  })

  it("displays the material ID in the header", async () => {
    renderView("test-material")

    await waitFor(() => {
      expect(screen.getByText(/test-material/)).toBeInTheDocument()
    })
  })

  it("shows not-found state when API returns 404", async () => {
    mockGetKGGraph.mockRejectedValue(new Error("KG node 'test-material' not found"))

    renderView("test-material")

    await waitFor(() => {
      expect(screen.getByText("节点未找到")).toBeInTheDocument()
    })
  })

  it("shows back-to-properties link in not-found state", async () => {
    mockGetKGGraph.mockRejectedValue(new Error("404"))

    renderView("test-material")

    await waitFor(() => {
      expect(screen.getByText("返回材料属性")).toBeInTheDocument()
    })
  })

  it("shows error state with retry button on generic failure", async () => {
    mockGetKGGraph.mockRejectedValue(new Error("Network error"))

    renderView("test-material")

    await waitFor(() => {
      expect(screen.getByText("加载失败")).toBeInTheDocument()
    })
    expect(screen.getByText("重试")).toBeInTheDocument()
  })

  it("retries fetch when retry button clicked", async () => {
    mockGetKGGraph
      .mockRejectedValueOnce(new Error("Network error"))
      .mockResolvedValueOnce(MOCK_RESPONSE)

    renderView("test-material")

    await waitFor(() => {
      expect(screen.getByText("加载失败")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("重试"))

    await waitFor(() => {
      expect(
        screen.getByRole("application", { name: /knowledge graph/i }),
      ).toBeInTheDocument()
    })
    expect(mockGetKGGraph).toHaveBeenCalledTimes(2)
  })

  it("has navigation links in header", () => {
    renderView("test-material")
    expect(screen.getByText("材料属性")).toBeInTheDocument()
    expect(screen.getByText("返回浏览")).toBeInTheDocument()
  })
})
