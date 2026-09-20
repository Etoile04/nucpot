import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { BrowseView } from "../BrowseView"

// ── API mocks ──────────────────────────────────────────────────────────

const { mockListPotentials } = vi.hoisted(() => ({
  mockListPotentials: vi.fn(),
}))

vi.mock("@/lib/potentials-api", () => ({
  listPotentials: (...args: unknown[]) => mockListPotentials(...args),
}))

// CompareBar (rendered by BrowseView) uses next/navigation's useRouter.
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
  }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  // /api/stats provides the element palette — served inside the standard
  // ApiResponse envelope and consumed by useElementOptions() (NFM-4310).
  global.fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ success: true, data: { elements: ["U", "Zr"] } }),
  })
  mockListPotentials.mockResolvedValue({
    potentials: [],
    total: 0,
    page: 1,
  })
})

describe("BrowseView 重置筛选 (NFM-4308 ①)", () => {
  it("reset clears element search text, selected elements, type checkboxes, and sort", async () => {
    render(<BrowseView />)

    // Wait for the element palette to load
    const searchInput = await screen.findByLabelText("搜索元素")
    expect(screen.getByText("U")).toBeInTheDocument()

    // Dirty every filter control
    fireEvent.change(searchInput, { target: { value: "U" } })
    fireEvent.click(screen.getByRole("button", { name: "U" })) // select element
    fireEvent.click(screen.getByLabelText("EAM")) // function-form checkbox
    fireEvent.change(screen.getByDisplayValue("最近更新"), {
      target: { value: "name" },
    }) // sort → 按名称

    expect(searchInput).toHaveValue("U")
    expect(screen.getByRole("button", { name: "U" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByLabelText("EAM")).toBeChecked()
    expect(screen.getByDisplayValue("按名称")).toBeInTheDocument()

    // Reset
    fireEvent.click(screen.getByRole("button", { name: "重置筛选" }))

    // Every control is back to its default
    expect(searchInput).toHaveValue("")
    expect(screen.getByRole("button", { name: "U" })).toHaveAttribute("aria-pressed", "false")
    expect(screen.getByLabelText("EAM")).not.toBeChecked()
    expect(screen.getByDisplayValue("最近更新")).toBeInTheDocument()
  })

  it("reset returns the list to page 1 even when every filter is already at its default", async () => {
    // NFM-4308 gate finding 3 — paginating without touching any filter
    // leaves all filter state at defaults; reset must still reload page 1.
    mockListPotentials.mockResolvedValue({
      potentials: [],
      total: 25, // PAGE_SIZE=12 → 3 pages
      page: 1,
    })
    render(<BrowseView />)

    await screen.findByLabelText("搜索元素")
    expect(mockListPotentials).toHaveBeenCalledTimes(1)

    // Paginate to page 2 with all filters untouched (rc-pagination
    // renders page items as <li title="N"> wrappers)
    fireEvent.click(screen.getByTitle("2"))
    await waitFor(() => {
      expect(mockListPotentials.mock.calls.at(-1)?.[0]?.page).toBe(2)
    })

    // Reset with all-default filters must still re-fire the page-1 load
    fireEvent.click(screen.getByRole("button", { name: "重置筛选" }))

    await waitFor(() => {
      expect(mockListPotentials.mock.calls.at(-1)?.[0]?.page).toBe(1)
    })
  })

  it("reset reloads the unfiltered first page", async () => {
    render(<BrowseView />)

    await screen.findByLabelText("搜索元素")
    fireEvent.click(screen.getByRole("button", { name: "Zr" }))
    fireEvent.click(screen.getByLabelText("MEAM"))
    fireEvent.change(screen.getByDisplayValue("最近更新"), {
      target: { value: "name" },
    })

    await waitFor(() => {
      const last = mockListPotentials.mock.calls.at(-1)?.[0] ?? {}
      expect(last.elements).toEqual(["Zr"])
      expect(last.type).toBe("MEAM")
      expect(last.sort).toBe("name")
    })

    fireEvent.click(screen.getByRole("button", { name: "重置筛选" }))

    await waitFor(() => {
      const last = mockListPotentials.mock.calls.at(-1)?.[0] ?? {}
      expect(last.elements).toBeUndefined()
      expect(last.type).toBeUndefined()
      expect(last.sort).toBe("updated")
      expect(last.page).toBe(1)
    })
  })
})
