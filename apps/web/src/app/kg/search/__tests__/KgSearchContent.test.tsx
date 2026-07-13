/**
 * Tests for KgSearchContent (NFM-1338 — refresh dispatch for /kg/search).
 *
 * Covers the acceptance criteria:
 *   - debounced search input (300ms)
 *   - optional type filter dropdown
 *   - GET /api/v1/kg/search fetch
 *   - result list with type badge + name
 *   - click → /kg/nodes/{type}/{id}
 *   - keyboard accessible
 *   - prefers-reduced-motion honored
 */

import {
  describe,
  it,
  expect,
  vi,
  beforeEach,
  afterEach,
} from "vitest"
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "@testing-library/react"
import { Suspense } from "react"

/**
 * Type into an input one character at a time so each keystroke fires
 * an `onChange`. Mirrors @testing-library/user-event's `.type()` semantics
 * without needing the user-event dependency.
 */
async function typeInto(input: HTMLElement, text: string) {
  for (const ch of text) {
    fireEvent.change(input, { target: { value: ((input as HTMLInputElement).value || "") + ch } })
  }
}

/**
 * Wait for real timers to advance. Used in place of fake-timer
 * `advanceTimersByTime` because microtasks + Promise resolution
 * interact poorly with fake timers in jsdom + AntD components.
 */
function tick(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/* ------------------------------------------------------------------ */
/*  Module mocks                                                      */
/* ------------------------------------------------------------------ */

const pushMock = vi.fn()
const replaceMock = vi.fn()
let mockSearchParamsString = ""

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
  useSearchParams: () => new URLSearchParams(mockSearchParamsString),
}))

const fetchKgSearchMock = vi.fn()
vi.mock("@/lib/kg-search-api", () => ({
  fetchKgSearch: (...args: unknown[]) => fetchKgSearchMock(...args),
  KG_NODE_TYPES: ["Material", "Property", "Experiment", "Condition", "Publication"],
}))

let mockReducedMotion = false
vi.mock("@/components/graph/useReducedMotion", () => ({
  useReducedMotion: () => mockReducedMotion,
}))

import { KgSearchContent } from "../KgSearchContent"
import type { KgSearchItem } from "@/lib/kg-search-api"

/* ------------------------------------------------------------------ */
/*  Test fixtures                                                     */
/* ------------------------------------------------------------------ */

const FIXTURE_ITEMS: KgSearchItem[] = [
  {
    id: "mat-1",
    node_type: "Material",
    label: "UO2",
    aliases: ["Uranium Dioxide"],
    properties: {},
    confidence: 0.95,
    status: "active",
    source_id: null,
  },
  {
    id: "prop-1",
    node_type: "Property",
    label: "Density",
    aliases: [],
    properties: { unit: "g/cm^3" },
    confidence: 0.88,
    status: "active",
    source_id: null,
  },
]

function makeResponse(
  items: KgSearchItem[] = [],
  total: number = items.length,
) {
  return { items, total, limit: 20, offset: 0 }
}

function renderInSuspense() {
  return render(
    <Suspense fallback={<div data-testid="suspense-fallback">loading</div>}>
      <KgSearchContent />
    </Suspense>,
  )
}

/* ------------------------------------------------------------------ */
/*  Setup / teardown                                                  */
/* ------------------------------------------------------------------ */

beforeEach(() => {
  pushMock.mockReset()
  replaceMock.mockReset()
  fetchKgSearchMock.mockReset()
  mockSearchParamsString = ""
  mockReducedMotion = false
  fetchKgSearchMock.mockResolvedValue(makeResponse())
})

afterEach(() => {
})

/* ------------------------------------------------------------------ */
/*  Initial render                                                    */
/* ------------------------------------------------------------------ */

describe("KgSearchContent — initial render", () => {
  it("renders header, input, and type-filter dropdown", async () => {
    renderInSuspense()

    expect(
      screen.getByRole("heading", { name: /knowledge graph search/i }),
    ).toBeInTheDocument()

    expect(
      screen.getByPlaceholderText(/search nodes by label or alias/i),
    ).toBeInTheDocument()

    // Type filter has "All Types" plus all 5 KG_NODE_TYPES
    const select = screen.getByRole("combobox", { name: /filter by node type/i }) as HTMLSelectElement
    const options = within(select).getAllByRole("option")
    expect(options.map((o) => o.textContent)).toEqual([
      "All Types",
      "Material",
      "Property",
      "Experiment",
      "Condition",
      "Publication",
    ])
  })

  it("renders empty-prompt state when no query entered", async () => {
    renderInSuspense()
    await waitFor(() => {
      expect(
        screen.getByText(/enter a search query or select a type to begin/i),
      ).toBeInTheDocument()
    })
    expect(fetchKgSearchMock).not.toHaveBeenCalled()
  })
})

/* ------------------------------------------------------------------ */
/*  Debounced search                                                  */
/* ------------------------------------------------------------------ */

describe("KgSearchContent — debounced search", () => {
  it("does not fetch immediately while typing", async () => {

    renderInSuspense()

    const input = screen.getByPlaceholderText(/search nodes by label or alias/i)
    await typeInto(input, "UO")

    // Within debounce window, no fetch yet.
    expect(fetchKgSearchMock).not.toHaveBeenCalled()

    // Advance past 300ms debounce.
    await tick(350)

    await waitFor(() => {
      expect(fetchKgSearchMock).toHaveBeenCalledTimes(1)
    })
    expect(fetchKgSearchMock).toHaveBeenCalledWith(
      expect.objectContaining({ q: "UO", offset: 0, limit: 20 }),
    )
  })

  it("renders fetched results with type badge and label", async () => {
    fetchKgSearchMock.mockResolvedValueOnce(makeResponse(FIXTURE_ITEMS, 2))

    renderInSuspense()

    const input = screen.getByPlaceholderText(/search nodes by label or alias/i)
    await typeInto(input, "density")

    await tick(350)

    await waitFor(() => {
      expect(screen.getByText(/2 results found/i)).toBeInTheDocument()
    })

    // Each result is rendered as a labeled button group.
    const uo2 = screen.getByRole("button", { name: /UO2/i })
    expect(within(uo2).getByText("Material")).toBeInTheDocument()
    expect(within(uo2).getByText("95%")).toBeInTheDocument()

    const density = screen.getByRole("button", { name: /Density/i })
    expect(within(density).getByText("Property")).toBeInTheDocument()
    expect(within(density).getByText("88%")).toBeInTheDocument()
  })

  it("shows loading spinner while fetching", async () => {
    let resolveFetch!: (value: unknown) => void
    fetchKgSearchMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFetch = resolve
      }),
    )


    const { container } = renderInSuspense()

    await typeInto(
      screen.getByPlaceholderText(/search nodes by label or alias/i),
      "UO",
    )
    await tick(350)

    // AntD <Spin tip="…" /> only renders the tip text in "nest" or
    // "fullscreen" patterns. The default pattern still exposes
    // `aria-busy="true"` on the wrapper, so we assert on that.
    await waitFor(() => {
      const spinner = container.querySelector('[aria-busy="true"]')
      expect(spinner).not.toBeNull()
    })

    // Resolve to clean up.
    resolveFetch(makeResponse(FIXTURE_ITEMS, 2))
  })

  it("shows error state with retry button when fetch rejects", async () => {
    fetchKgSearchMock.mockRejectedValueOnce(new Error("Network down"))


    renderInSuspense()

    await typeInto(
      screen.getByPlaceholderText(/search nodes by label or alias/i),
      "UO",
    )
    await tick(350)

    await waitFor(() => {
      expect(screen.getByText(/network down/i)).toBeInTheDocument()
    })
    expect(
      screen.getByRole("button", { name: /retry/i }),
    ).toBeInTheDocument()
  })

  it("renders empty-results state when API returns no items", async () => {
    fetchKgSearchMock.mockResolvedValueOnce(makeResponse([], 0))


    renderInSuspense()

    await typeInto(
      screen.getByPlaceholderText(/search nodes by label or alias/i),
      "nothingmatches",
    )
    await tick(350)

    await waitFor(() => {
      expect(screen.getByText(/no results found/i)).toBeInTheDocument()
    })
  })
})

/* ------------------------------------------------------------------ */
/*  Type filter                                                       */
/* ------------------------------------------------------------------ */

describe("KgSearchContent — type filter", () => {
  it("changing type triggers fetch with type parameter", async () => {

    renderInSuspense()

    // Input a query first so url has a basis for the filter call.
    const input = screen.getByPlaceholderText(/search nodes by label or alias/i)
    await typeInto(input, "density")
    await tick(350)

    // Now switch the type dropdown.
    fireEvent.change(screen.getByRole("combobox", { name: /filter by node type/i }), {
      target: { value: "Material" },
    })
    await tick(350)

    await waitFor(() => {
      const calls = fetchKgSearchMock.mock.calls
      // Last call should include the type filter
      const last = calls[calls.length - 1]?.[0] as { type?: string } | undefined
      expect(last?.type).toBe("Material")
    })
  })
})

/* ------------------------------------------------------------------ */
/*  URL sync                                                          */
/* ------------------------------------------------------------------ */

describe("KgSearchContent — URL sync", () => {
  it("debounced query syncs to URL via router.replace", async () => {

    renderInSuspense()

    await typeInto(
      screen.getByPlaceholderText(/search nodes by label or alias/i),
      "UO",
    )
    await tick(350)

    await waitFor(() => {
      expect(replaceMock).toHaveBeenCalled()
    })
    // `syncUrl` is fired per-keystroke, so multiple `replace` calls happen.
    // The last call should reflect the full typed value.
    const urls = replaceMock.mock.calls.map((c) => c[0] as string)
    const lastKgSearchCall = [...urls].reverse().find((u) => u.includes("/kg/search"))
    expect(lastKgSearchCall).toBeDefined()
    expect(lastKgSearchCall).toMatch(/q=UO/)
  })

  it("reads initial q and type from useSearchParams on mount (deep-link)", async () => {
    mockSearchParamsString = "q=initial&type=Property"
    fetchKgSearchMock.mockResolvedValueOnce(makeResponse(FIXTURE_ITEMS, 2))

    renderInSuspense()

    // Input value seeded from URL.
    const input =
      screen.getByPlaceholderText(/search nodes by label or alias/i)
    expect((input as HTMLInputElement).value).toBe("initial")

    // Dropdown seeded from URL.
    const select = screen.getByRole("combobox", { name: /filter by node type/i }) as HTMLSelectElement
    expect(select.value).toBe("Property")

    // Search fires on mount with both params.
    await waitFor(() => {
      expect(fetchKgSearchMock).toHaveBeenCalled()
    })
    const params = fetchKgSearchMock.mock.calls[0]?.[0] as
      | { q?: string; type?: string }
      | undefined
    expect(params?.q).toBe("initial")
    expect(params?.type).toBe("Property")
  })
})

/* ------------------------------------------------------------------ */
/*  Result click → navigation                                         */
/* ------------------------------------------------------------------ */

describe("KgSearchContent — result click", () => {
  it("clicking a Material result calls router.push /kg/nodes/Material/{id}", async () => {
    fetchKgSearchMock.mockResolvedValueOnce(makeResponse(FIXTURE_ITEMS, 2))

    renderInSuspense()

    await typeInto(
      screen.getByPlaceholderText(/search nodes by label or alias/i),
      "UO",
    )
    await tick(350)

    const uo2Button = await screen.findByRole("button", { name: /UO2/i })
    fireEvent.click(uo2Button)

    expect(pushMock).toHaveBeenCalledWith("/kg/nodes/Material/mat-1")
  })

  it("clicking a Property result calls router.push /kg/nodes/Property/{id}", async () => {
    fetchKgSearchMock.mockResolvedValueOnce(makeResponse(FIXTURE_ITEMS, 2))

    renderInSuspense()

    await typeInto(
      screen.getByPlaceholderText(/search nodes by label or alias/i),
      "UO",
    )
    await tick(350)

    const density = await screen.findByRole("button", { name: /Density/i })
    fireEvent.click(density)

    expect(pushMock).toHaveBeenCalledWith("/kg/nodes/Property/prop-1")
  })
})

/* ------------------------------------------------------------------ */
/*  Accessibility                                                     */
/* ------------------------------------------------------------------ */

describe("KgSearchContent — accessibility", () => {
  it("renders results as <button> elements (keyboard accessible)", async () => {
    fetchKgSearchMock.mockResolvedValueOnce(makeResponse(FIXTURE_ITEMS, 2))

    renderInSuspense()
    await typeInto(
      screen.getByPlaceholderText(/search nodes by label or alias/i),
      "UO",
    )
    await tick(350)

    const buttons = await screen.findAllByRole("button")
    // Each result is a real <button>, so it is reachable by Tab and
    // activated by Enter (native button semantics).
    const resultButtons = buttons.filter(
      (b) => b.textContent?.includes("UO2") || b.textContent?.includes("Density"),
    )
    expect(resultButtons.length).toBeGreaterThanOrEqual(2)
    for (const b of resultButtons) {
      expect(b.tagName).toBe("BUTTON")
    }
  })
})

/* ------------------------------------------------------------------ */
/*  prefers-reduced-motion                                            */
/* ------------------------------------------------------------------ */

describe("KgSearchContent — prefers-reduced-motion", () => {
  it("strips Tailwind transition-* classes when reduced motion is preferred", () => {
    mockReducedMotion = true
    fetchKgSearchMock.mockResolvedValue(makeResponse(FIXTURE_ITEMS, 2))

    renderInSuspense()

    // Inspect the search input and select that they no longer carry
    // `transition-*` utility classes when motion is reduced.
    const input = screen.getByPlaceholderText(
      /search nodes by label or alias/i,
    )
    expect(input.className).not.toMatch(/transition-/)

    const select = screen.getByRole("combobox", { name: /filter by node type/i })
    expect(select.className).not.toMatch(/transition-/)
  })

  it("keeps transition-* classes when motion is allowed", () => {
    mockReducedMotion = false
    fetchKgSearchMock.mockResolvedValue(makeResponse(FIXTURE_ITEMS, 2))

    renderInSuspense()

    const input = screen.getByPlaceholderText(
      /search nodes by label or alias/i,
    )
    expect(input.className).toMatch(/transition-/)
  })
})
