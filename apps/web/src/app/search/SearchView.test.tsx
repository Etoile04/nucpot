import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { SearchView } from "./SearchView"

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
// NFM-4310 (BUG-29) regression: the element filter must render the library's
// element list from the stats envelope, and a stats outage must render an
// error state with retry — never the 「无匹配元素」 empty state.
// ---------------------------------------------------------------------------
describe("SearchView element filter (NFM-4310)", () => {
  it("renders element candidates from the ApiResponse envelope", async () => {
    routeFetch(
      jsonResponse(200, {
        success: true,
        data: { elements: ["Mo", "O", "Ta", "U", "W"] },
      })
    )

    render(<SearchView />)

    expect(await screen.findByRole("button", { name: "U" })).toBeDefined()
    expect(screen.getByRole("button", { name: "Ta" })).toBeDefined()
    expect(screen.queryByText("无匹配元素")).toBeNull()
  })

  it("shows an error state with retry (not the empty state) when stats fail", async () => {
    routeFetch(jsonResponse(502, { success: false, error: "统计服务不可用" }))

    render(<SearchView />)

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

    render(<SearchView />)
    await screen.findByRole("alert")

    statsFailing = false
    fireEvent.click(screen.getByRole("button", { name: "重试" }))

    await waitFor(() => {
      expect(screen.queryByRole("alert")).toBeNull()
    })
    expect(await screen.findByRole("button", { name: "U" })).toBeDefined()
  })

  it("checks U → queries elements=U, composable with the type filter", async () => {
    routeFetch(
      jsonResponse(200, {
        success: true,
        data: { elements: ["O", "U", "Zr"] },
      })
    )

    render(<SearchView />)
    await screen.findByRole("button", { name: "U" })

    // 勾选 U → 列表请求带 elements=U
    fireEvent.click(screen.getByRole("button", { name: "U" }))
    await waitFor(() => {
      const listCalls = mockFetch.mock.calls.map(([input]) => String(input)).filter(u => u.startsWith("/api/potentials"))
      expect(listCalls.some(u => u.includes("elements=U"))).toBe(true)
    })

    // 组合函数形式筛选 → 请求同时带 type=EAM 与 elements=U
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "EAM" } })
    await waitFor(() => {
      const listCalls = mockFetch.mock.calls.map(([input]) => String(input)).filter(u => u.startsWith("/api/potentials"))
      expect(
        listCalls.some(u => u.includes("elements=U") && u.includes("type=EAM"))
      ).toBe(true)
    })
  })
})
