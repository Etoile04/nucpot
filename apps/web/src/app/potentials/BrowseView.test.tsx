import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { BrowseView } from "./BrowseView"

// CompareBar uses next/navigation's useRouter, which requires the App
// Router context — stub it for component-level rendering.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}))

// ---------------------------------------------------------------------------
// Mock global.fetch: route by URL — /api/stats feeds the element filter,
// /api/potentials feeds the result grid.
// ---------------------------------------------------------------------------
const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

const POTENTIALS_BODY = {
  potentials: [
    {
      id: "pot-001",
      name: "UO2-EAM",
      type: "EAM",
      elements: ["U", "O"],
      description: null,
      version: null,
      tags: [],
    },
  ],
  total: 1,
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function routeFetch(stats: Response): void {
  mockFetch.mockImplementation((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.startsWith("/api/stats")) return Promise.resolve(stats)
    if (url.startsWith("/api/potentials")) {
      return Promise.resolve(jsonResponse(200, POTENTIALS_BODY))
    }
    return Promise.reject(new Error(`unexpected fetch: ${url}`))
  })
}

beforeEach(() => {
  mockFetch.mockReset()
})

// ---------------------------------------------------------------------------
// NFM-4310 (BUG-29) regression: same contract as SearchView — the browse
// sidebar element filter renders library elements from the stats envelope,
// and a stats outage renders an error state with retry, not 「无匹配元素」.
// ---------------------------------------------------------------------------
describe("BrowseView element filter (NFM-4310)", () => {
  it("renders element candidates from the ApiResponse envelope", async () => {
    routeFetch(
      jsonResponse(200, {
        success: true,
        data: { elements: ["Mo", "O", "Ta", "U", "W"] },
      })
    )

    render(<BrowseView />)

    expect(await screen.findByRole("button", { name: "U" })).toBeDefined()
    expect(screen.getByRole("button", { name: "Ta" })).toBeDefined()
    expect(screen.queryByText("无匹配元素")).toBeNull()
  })

  it("shows an error state with retry (not the empty state) when stats fail", async () => {
    routeFetch(jsonResponse(502, { success: false, error: "统计服务不可用" }))

    render(<BrowseView />)

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("502")
    expect(screen.getByRole("button", { name: "重试" })).toBeDefined()
    expect(screen.queryByText("无匹配元素")).toBeNull()
  })

  it("retry recovers the element list after a failure", async () => {
    let statsFailing = true
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith("/api/stats")) {
        return statsFailing
          ? Promise.resolve(
              jsonResponse(502, { success: false, error: "统计服务不可用" })
            )
          : Promise.resolve(
              jsonResponse(200, {
                success: true,
                data: { elements: ["U", "W"] },
              })
            )
      }
      if (url.startsWith("/api/potentials")) {
        return Promise.resolve(jsonResponse(200, POTENTIALS_BODY))
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`))
    })

    render(<BrowseView />)
    await screen.findByRole("alert")

    statsFailing = false
    fireEvent.click(screen.getByRole("button", { name: "重试" }))

    await waitFor(() => {
      expect(screen.queryByRole("alert")).toBeNull()
    })
    expect(await screen.findByRole("button", { name: "U" })).toBeDefined()
  })
})
