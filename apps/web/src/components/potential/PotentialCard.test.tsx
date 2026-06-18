import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { PotentialCard } from "./PotentialCard"
import type { PotentialSummary } from "@/lib/potentials-api"

const mockPotential: PotentialSummary = {
  id: "pot-001",
  name: "Nb-EAM-FS-001",
  type: "EAM",
  elements: ["Nb", "Zr"],
  description: "Finnis-Sinclair EAM potential for Nb-Zr alloys.",
  version: "1.0",
  tags: ["reactor-fuel"],
}

describe("PotentialCard", () => {
  it("renders the potential name and type tag", () => {
    render(<PotentialCard potential={mockPotential} />)

    expect(screen.getByText("Nb-EAM-FS-001")).toBeDefined()
    expect(screen.getByText("EAM")).toBeDefined()
  })

  it("renders element tags", () => {
    render(<PotentialCard potential={mockPotential} />)

    expect(screen.getByText("Nb")).toBeDefined()
    expect(screen.getByText("Zr")).toBeDefined()
  })

  it("renders description snippet", () => {
    render(<PotentialCard potential={mockPotential} />)

    expect(
      screen.getByText(/Finnis-Sinclair EAM potential for Nb-Zr alloys/),
    ).toBeDefined()
  })

  it("renders a detail page link", () => {
    render(<PotentialCard potential={mockPotential} />)

    const links = screen.getAllByText("查看详情")
    expect(links.length).toBeGreaterThan(0)
    expect(links[0]?.closest("a")).toBeTruthy()
  })

  it("links to the detail page by id", () => {
    render(<PotentialCard potential={mockPotential} />)

    const nameLink = screen.getByText("Nb-EAM-FS-001").closest("a")
    expect(nameLink?.getAttribute("href")).toBe("/potential/pot-001")
  })
})
