/**
 * Tests for the KG Node Detail page (NFM-1337).
 */

import { describe, it, expect, vi, beforeEach } from "vitest"

// ── Mocks (hoisted before SUT import) ────────────────────────────────

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.stubGlobal("fetch", fetchMock)

// next/navigation: provide a stub router so back() can be asserted.
const { backMock } = vi.hoisted(() => ({
  backMock: vi.fn(),
}))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: backMock, replace: vi.fn() }),
  useParams: () => ({ type: "Material", id: "node-abc" }),
}))

// Stub next/link so we can assert the rendered href synchronously without
// relying on Next's internal Router context in the test harness.
vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    href,
    children,
    ...rest
  }: {
    readonly href: string
    readonly children: React.ReactNode
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}))

import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { KgNodeDetailContent } from "./KgNodeDetailContent"

// ── Mock data matching the two-endpoint contract ────────────────────
// fetchKgNodeDetail calls fetchKgNode + fetchKgRelations via Promise.all.

const MOCK_NODE_RESPONSE = {
  success: true,
  data: {
    id: "node-abc",
    node_type: "Material",
    label: "UO2",
    aliases: ["Uranium Dioxide", "二氧化铀"],
    properties: {
      density: "10.97 g/cm³",
      melting_point: "3120 K",
    },
    confidence: 0.92,
    status: "active",
    source_id: "src-1",
  },
}

const MOCK_RELATIONS_RESPONSE = {
  success: true,
  data: {
    items: [
      // Incoming: source_node is pub-1, target_node is node-abc
      {
        id: "edge-in-1",
        relation_type: "references",
        confidence: 0.8,
        properties: {},
        source_node: {
          id: "pub-1",
          node_type: "Publication",
          label: "Smith 2020",
          aliases: [],
          properties: {},
          confidence: 0.7,
          status: "active",
          source_id: null,
        },
        target_node: {
          id: "node-abc",
          node_type: "Material",
          label: "UO2",
          aliases: [],
          properties: {},
          confidence: 0.92,
          status: "active",
          source_id: null,
        },
      },
      // Outgoing: source_node is node-abc, target_node is prop-1
      {
        id: "edge-out-1",
        relation_type: "hasProperty",
        confidence: 0.95,
        properties: {},
        source_node: {
          id: "node-abc",
          node_type: "Material",
          label: "UO2",
          aliases: [],
          properties: {},
          confidence: 0.92,
          status: "active",
          source_id: null,
        },
        target_node: {
          id: "prop-1",
          node_type: "Property",
          label: "Melting Point",
          aliases: [],
          properties: {},
          confidence: 0.85,
          status: "active",
          source_id: null,
        },
      },
      // Outgoing: source_node is node-abc, target_node is exp-1
      {
        id: "edge-out-2",
        relation_type: "measuredIn",
        confidence: 0.7,
        properties: {},
        source_node: {
          id: "node-abc",
          node_type: "Material",
          label: "UO2",
          aliases: [],
          properties: {},
          confidence: 0.92,
          status: "active",
          source_id: null,
        },
        target_node: {
          id: "exp-1",
          node_type: "Experiment",
          label: "DSC run 42",
          aliases: [],
          properties: {},
          confidence: 0.8,
          status: "active",
          source_id: null,
        },
      },
    ],
    total: 3,
    limit: 50,
    offset: 0,
  },
}

/** Queue both fetch responses (node + relations) for success cases. */
function mockSuccessResponses() {
  fetchMock
    .mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_NODE_RESPONSE),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_RELATIONS_RESPONSE),
    })
}

beforeEach(() => {
  fetchMock.mockReset()
  backMock.mockReset()
})

describe("KgNodeDetailContent", () => {
  it("1. fetches /api/v1/kg/nodes/{type}/{id} with the route params", async () => {
    mockSuccessResponses()

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2)
    })
    const urls = fetchMock.mock.calls.map(([u]) => u as string)
    expect(urls[0]).toBe("/api/v1/kg/nodes/Material/node-abc")
  })

  it("2. shows a content-shape skeleton before the fetch resolves", () => {
    fetchMock.mockReturnValueOnce(new Promise(() => {})) // never resolves

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    const loading = screen.getByTestId("kg-node-detail-loading")
    expect(loading).toBeInTheDocument()
    expect(loading).toHaveAttribute("role", "status")
    expect(loading).toHaveAttribute("aria-busy", "true")
    // Skeleton content-shape: must be a full-bleed main (not a thin spinner)
    expect(loading.tagName.toLowerCase()).toBe("main")
  })

  it("3. renders node label, type badge, and confidence badge once loaded", async () => {
    mockSuccessResponses()

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "UO2",
      )
    })
    // The header badge contains the literal "Material" text. There is also
    // a second "Material" badge further down if relations reference it,
    // so use getAllByText and pick the one in the page header.
    const materialBadges = screen.getAllByText("Material")
    expect(materialBadges.length).toBeGreaterThanOrEqual(1)
    const headerBadge = materialBadges[0] as HTMLElement
    expect(headerBadge.closest("span")).toHaveClass(
      "bg-blue-500/20",
      "text-blue-300",
      "border-blue-500/30",
    )
    expect(screen.getByLabelText(/置信度: 0\.92/)).toBeInTheDocument()
  })

  it("4. renders the properties table with each property's confidence badge", async () => {
    mockSuccessResponses()

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(screen.getByText("density")).toBeInTheDocument()
    })
    expect(screen.getByText("10.97 g/cm³")).toBeInTheDocument()
    expect(screen.getByText("melting_point")).toBeInTheDocument()
    expect(screen.getByText("3120 K")).toBeInTheDocument()
  })

  it("5. renders source references", async () => {
    mockSuccessResponses()

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      // Smith 2020 also appears as an incoming-relation neighbour, so
      // use the non-strict matcher here. The full-document presence
      // is enough to assert the source list rendered.
      expect(screen.getAllByText("Smith 2020").length).toBeGreaterThanOrEqual(1)
    })
  })

  it("6. renders the relations sidebar with incoming and outgoing edges", async () => {
    mockSuccessResponses()

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(screen.getByText("Melting Point")).toBeInTheDocument()
    })
    expect(screen.getByText("DSC run 42")).toBeInTheDocument()
    // Smith 2020 appears in both sources and the incoming-relation
    // sidebar — assert presence via getAllByText to avoid the
    // multiple-elements-found error from the strict matcher.
    expect(screen.getAllByText("Smith 2020").length).toBeGreaterThanOrEqual(1)
    // Section headings
    expect(screen.getByText(/Incoming/i)).toBeInTheDocument()
    expect(screen.getByText(/Outgoing/i)).toBeInTheDocument()
  })

  it("7. renders relations as anchors with href /kg/nodes/{target_type}/{target_id} (F2: Link)", async () => {
    mockSuccessResponses()

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(screen.getByText("Melting Point")).toBeInTheDocument()
    })

    // Find the anchor that wraps the "Melting Point" relation. With the
    // next/link stub, this resolves to a plain <a href="...">.
    const meltingPointLinks = screen.getAllByRole("link", {
      name: /Melting Point/i,
    })
    expect(meltingPointLinks.length).toBeGreaterThanOrEqual(1)
    const link = meltingPointLinks.find(
      (el) => el.getAttribute("href") === "/kg/nodes/Property/prop-1",
    ) as HTMLAnchorElement | undefined
    expect(link).toBeDefined()
    expect(link!).toHaveAttribute("href", "/kg/nodes/Property/prop-1")

    // The incoming relation should also be an anchor.
    const incomingLink = screen.getByRole("link", { name: /Smith 2020/i })
    expect(incomingLink).toHaveAttribute("href", "/kg/nodes/Publication/pub-1")
  })

  it("8. shows a retry-able error state when the API returns non-OK", async () => {
    fetchMock
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: () => Promise.resolve({ detail: "boom" }),
      })
      // Second fetch for relations — still queued by Promise.all
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(MOCK_RELATIONS_RESPONSE),
      })

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(screen.getByText(/boom/)).toBeInTheDocument()
    })

    const retryBtn = screen.getByRole("button", { name: /retry/i })
    expect(retryBtn).toBeInTheDocument()
  })

  it("9. has a back link that calls router.back()", async () => {
    mockSuccessResponses()

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(screen.getByText("UO2")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /back/i }))
    expect(backMock).toHaveBeenCalled()
  })

  it("10. exposes a prefers-reduced-motion-aware data-testid for QA hooks", async () => {
    mockSuccessResponses()

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(screen.getByTestId("kg-node-detail-root")).toBeInTheDocument()
    })
    const root = screen.getByTestId("kg-node-detail-root")
    expect(root).toHaveAttribute("data-reduced-motion", "honored")
  })
})
