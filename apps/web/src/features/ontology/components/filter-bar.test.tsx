/**
 * Tests for FilterBar component.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { FilterBar } from "./filter-bar"

describe("FilterBar", () => {
  const onChange = vi.fn()

  beforeEach(() => { vi.clearAllMocks(); onChange.mockClear() })

  it("renders all status filter buttons", () => {
    render(<FilterBar current="all" onChange={onChange} />)
    expect(screen.getByText("All")).toBeInTheDocument()
    expect(screen.getByText("Draft")).toBeInTheDocument()
    expect(screen.getByText("Published")).toBeInTheDocument()
    expect(screen.getByText("Deprecated")).toBeInTheDocument()
  })

  it("marks current filter as active via aria-pressed", () => {
    render(<FilterBar current="published" onChange={onChange} />)
    expect(screen.getByText("Published")).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByText("All")).toHaveAttribute("aria-pressed", "false")
  })

  it("calls onChange when a filter is clicked", async () => {
        render(<FilterBar current="all" onChange={onChange} />)
    await fireEvent.click(screen.getByText("Draft"))
    expect(onChange).toHaveBeenCalledWith("draft")
  })

  it("has aria-label on the navigation landmark", () => {
    render(<FilterBar current="all" onChange={onChange} />)
    // NFM-3794 a11y fix: the outer container is a <nav> landmark, not a
    // group. `role="group"` on a <nav> is rejected by `aria-allowed-role`
    // (group is only allowed on a small set of inline elements per ARIA).
    const nav = screen.getByRole("navigation", { name: "Status filter" })
    expect(nav).toBeInTheDocument()
  })
})
