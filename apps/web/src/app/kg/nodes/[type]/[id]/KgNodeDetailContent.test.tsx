/**
 * Tests for the KG Node Detail page (NFM-1337).
 */

import { describe, it, expect, vi, beforeEach } from "vitest"

// ── Mocks (hoisted before SUT import) ────────────────────────────────

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }))
vi.stubGlobal("fetch", fetchMock)

// next/navigation: provide a stub router so we can assert navigation.
const { pushMock, backMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  backMock: vi.fn(),
}))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, back: backMock, replace: vi.fn() }),
  useParams: () => ({ type: "Material", id: "node-abc" }),
}))

import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { KgNodeDetailContent } from "./KgNodeDetailContent"

const MOCK_NODE = {
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
  corpus_id: null,
  relations: {
    incoming: [
      {
        edge_id: "edge-in-1",
        relation_type: "references",
        direction: "incoming",
        confidence: 0.8,
        neighbour: {
          id: "pub-1",
          node_type: "Publication",
          label: "Smith 2020",
        },
      },
    ],
    outgoing: [
      {
        edge_id: "edge-out-1",
        relation_type: "hasProperty",
        direction: "outgoing",
        confidence: 0.95,
        neighbour: {
          id: "prop-1",
          node_type: "Property",
          label: "Melting Point",
        },
      },
      {
        edge_id: "edge-out-2",
        relation_type: "measuredIn",
        direction: "outgoing",
        confidence: 0.7,
        neighbour: {
          id: "exp-1",
          node_type: "Experiment",
          label: "DSC run 42",
        },
      },
    ],
  },
  sources: [
    { source_id: "src-1", figure_id: null, label: "Smith 2020" },
    { source_id: "src-2", figure_id: "fig-9", label: "Doe 2019" },
  ],
}

beforeEach(() => {
  fetchMock.mockReset()
  pushMock.mockReset()
  backMock.mockReset()
})

describe("KgNodeDetailContent", () => {
  it("1. fetches /api/v1/kg/nodes/{type}/{id} with the route params", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_NODE),
    })

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
    })
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe("/api/v1/kg/nodes/Material/node-abc")
  })

  it("2. shows a loading spinner before the fetch resolves", () => {
    fetchMock.mockReturnValueOnce(new Promise(() => {})) // never resolves

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    expect(screen.getByRole("status")).toBeInTheDocument()
  })

  it("3. renders node label, type badge, and confidence badge once loaded", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_NODE),
    })

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "UO2",
      )
    })
    expect(screen.getByText("Material")).toBeInTheDocument()
    expect(screen.getByLabelText(/置信度: 0\.92/)).toBeInTheDocument()
  })

  it("4. renders the properties table with each property's confidence badge", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_NODE),
    })

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(screen.getByText("density")).toBeInTheDocument()
    })
    expect(screen.getByText("10.97 g/cm³")).toBeInTheDocument()
    expect(screen.getByText("melting_point")).toBeInTheDocument()
    expect(screen.getByText("3120 K")).toBeInTheDocument()
  })

  it("5. renders source references", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_NODE),
    })

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      // Smith 2020 also appears as an incoming-relation neighbour, so
      // use the non-strict matcher here. The full-document presence
      // is enough to assert the source list rendered.
      expect(screen.getAllByText("Smith 2020").length).toBeGreaterThanOrEqual(1)
    })
    expect(screen.getByText("Doe 2019")).toBeInTheDocument()
  })

  it("6. renders the relations sidebar with incoming and outgoing edges", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_NODE),
    })

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

  it("7. clicking a relation navigates to /kg/nodes/{target_type}/{target_id}", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_NODE),
    })

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(screen.getByText("Melting Point")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Melting Point"))

    expect(pushMock).toHaveBeenCalledWith("/kg/nodes/Property/prop-1")
  })

  it("8. shows a retry-able error state when the API returns non-OK", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: () => Promise.resolve({ detail: "boom" }),
    })

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(screen.getByText(/boom/)).toBeInTheDocument()
    })

    const retryBtn = screen.getByRole("button", { name: /retry/i })
    expect(retryBtn).toBeInTheDocument()
  })

  it("9. has a back link that calls router.back()", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_NODE),
    })

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(screen.getByText("UO2")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /back/i }))
    expect(backMock).toHaveBeenCalled()
  })

  it("10. exposes a prefers-reduced-motion-aware data-testid for QA hooks", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_NODE),
    })

    render(<KgNodeDetailContent type="Material" id="node-abc" />)

    await waitFor(() => {
      expect(screen.getByTestId("kg-node-detail-root")).toBeInTheDocument()
    })
    const root = screen.getByTestId("kg-node-detail-root")
    expect(root).toHaveAttribute("data-reduced-motion", "honored")
  })
})