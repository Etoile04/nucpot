/**
 * KgLegend — renders node-type color legend mapped to design tokens.
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { KgLegend } from "../KgLegend"

describe("KgLegend", () => {
  it("renders all four node types with labels", () => {
    render(<KgLegend />)

    expect(screen.getByText("Material")).toBeInTheDocument()
    expect(screen.getByText("Property")).toBeInTheDocument()
    expect(screen.getByText("Entity")).toBeInTheDocument()
    expect(screen.getByText("Other")).toBeInTheDocument()
  })

  it("maps colors to design token CSS variables", () => {
    const { container } = render(<KgLegend />)

    const swatches = container.querySelectorAll("[data-testid='legend-swatch']")
    expect(swatches).toHaveLength(4)

    // material → --graph-node-material (#34d399)
    expect(swatches[0]).toHaveStyle({
      backgroundColor: "var(--graph-node-material)",
    })
    // property → --graph-node-property (#fbbf24)
    expect(swatches[1]).toHaveStyle({
      backgroundColor: "var(--graph-node-property)",
    })
    // entity → --graph-node-entity (#a78bfa)
    expect(swatches[2]).toHaveStyle({
      backgroundColor: "var(--graph-node-entity)",
    })
    // default → --graph-node-default (#60a5fa)
    expect(swatches[3]).toHaveStyle({
      backgroundColor: "var(--graph-node-default)",
    })
  })

  it("has accessible role as complementary landmark", () => {
    render(<KgLegend />)
    const legend = screen.getByRole("complementary", { name: /graph legend/i })
    expect(legend).toBeInTheDocument()
  })

  it("applies prefers-reduced-motion styles", () => {
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

    const { container } = render(<KgLegend />)
    const legend = container.firstChild as HTMLElement
    expect(legend).toHaveStyle({ transition: "none" })

    vi.restoreAllMocks()
  })
})
