/**
 * KgToolbar — zoom in/out/fit controls and type filter dropdown.
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { KgToolbar } from "../KgToolbar"
import type { GraphNodeType } from "@/components/graph/types"

describe("KgToolbar", () => {
  const defaultProps = {
    onZoomIn: vi.fn(),
    onZoomOut: vi.fn(),
    onFit: vi.fn(),
    onToggleType: vi.fn(),
    activeTypes: new Set<GraphNodeType>([
      "material",
      "property",
      "entity",
      "default",
    ]),
  }

  it("renders zoom in, zoom out, and fit buttons", () => {
    render(<KgToolbar {...defaultProps} />)

    expect(
      screen.getByRole("button", { name: /zoom in/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /zoom out/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /fit to view/i }),
    ).toBeInTheDocument()
  })

  it("renders type filter dropdown", () => {
    render(<KgToolbar {...defaultProps} />)

    expect(
      screen.getByRole("combobox", { name: /filter by type/i }),
    ).toBeInTheDocument()
  })

  it("calls onZoomIn when zoom in button is clicked", () => {
    const onZoomIn = vi.fn()
    render(<KgToolbar {...defaultProps} onZoomIn={onZoomIn} />)

    fireEvent.click(screen.getByRole("button", { name: /zoom in/i }))
    expect(onZoomIn).toHaveBeenCalledOnce()
  })

  it("calls onZoomOut when zoom out button is clicked", () => {
    const onZoomOut = vi.fn()
    render(<KgToolbar {...defaultProps} onZoomIn={vi.fn()} onZoomOut={onZoomOut} />)

    fireEvent.click(screen.getByRole("button", { name: /zoom out/i }))
    expect(onZoomOut).toHaveBeenCalledOnce()
  })

  it("calls onFit when fit button is clicked", () => {
    const onFit = vi.fn()
    render(<KgToolbar {...defaultProps} onFit={onFit} />)

    fireEvent.click(screen.getByRole("button", { name: /fit to view/i }))
    expect(onFit).toHaveBeenCalledOnce()
  })

  it("calls onToggleType when a type option is selected", () => {
    const onToggleType = vi.fn()
    render(<KgToolbar {...defaultProps} onToggleType={onToggleType} />)

    const select = screen.getByRole("combobox", { name: /filter by type/i })
    fireEvent.change(select, { target: { value: "material" } })

    expect(onToggleType).toHaveBeenCalledWith("material")
  })

  it("buttons are keyboard accessible — native button with type=button", () => {
    render(<KgToolbar {...defaultProps} />)

    const zoomInBtn = screen.getByRole("button", { name: /zoom in/i })

    // Native <button> elements are keyboard-accessible by default.
    // Verify the element exists and has type="button" (not submit).
    expect(zoomInBtn.tagName.toLowerCase()).toBe("button")
    expect(zoomInBtn).toHaveAttribute("type", "button")

    // Verify click still works
    fireEvent.click(zoomInBtn)
    expect(defaultProps.onZoomIn).toHaveBeenCalledOnce()

    // Verify focusable
    zoomInBtn.focus()
    expect(document.activeElement).toBe(zoomInBtn)
  })

  it("shows active type count in button label", () => {
    const twoTypes = new Set<GraphNodeType>(["material", "property"])
    render(<KgToolbar {...defaultProps} activeTypes={twoTypes} />)

    expect(screen.getByText(/2 \/ 4 types/i)).toBeInTheDocument()
  })
})
