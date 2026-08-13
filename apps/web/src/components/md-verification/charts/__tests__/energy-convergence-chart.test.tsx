import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { EnergyConvergenceChart } from "@/components/md-verification/charts/energy-convergence-chart"

const ENERGY_ONLY_DATA = {
  energy: [
    { step: 0, energy: -245.3 },
    { step: 100, energy: -245.1 },
    { step: 200, energy: -244.8 },
    { step: 500, energy: -244.2 },
  ],
}

const MULTI_AXIS_DATA = {
  energy: [
    { step: 0, energy: -245.3 },
    { step: 100, energy: -245.1 },
    { step: 200, energy: -244.8 },
  ],
  temperature: [
    { step: 0, temperature: 300 },
    { step: 100, temperature: 305 },
    { step: 200, temperature: 298 },
  ],
  pressure: [
    { step: 0, pressure: 0.0 },
    { step: 100, pressure: 0.5 },
    { step: 200, pressure: 0.3 },
  ],
}

const EMPTY_DATA = {}

describe("EnergyConvergenceChart", () => {
  it("renders nothing when all data arrays are empty", () => {
    const { container } = render(<EnergyConvergenceChart thermoData={EMPTY_DATA} />)
    expect(container.innerHTML).toBe("")
  })

  it("renders nothing with completely empty object", () => {
    const { container } = render(<EnergyConvergenceChart thermoData={{}} />)
    expect(container.innerHTML).toBe("")
  })

  it("renders chart with energy data only", () => {
    render(<EnergyConvergenceChart thermoData={ENERGY_ONLY_DATA} />)
    const chart = screen.getByTestId("energy-convergence-chart")
    expect(chart).toBeInTheDocument()
  })

  it("renders chart with multiple axes (energy + temperature + pressure)", () => {
    render(<EnergyConvergenceChart thermoData={MULTI_AXIS_DATA} />)
    expect(screen.getByTestId("energy-convergence-chart")).toBeInTheDocument()
  })

  it("uses default height of 300px", () => {
    render(<EnergyConvergenceChart thermoData={ENERGY_ONLY_DATA} />)
    const chart = screen.getByTestId("energy-convergence-chart")
    expect(chart.style.height).toBe("300px")
  })

  it("applies custom height and className", () => {
    render(
      <EnergyConvergenceChart
        thermoData={ENERGY_ONLY_DATA}
        height={200}
        className="thermal"
      />,
    )
    const chart = screen.getByTestId("energy-convergence-chart")
    expect(chart.style.height).toBe("200px")
    expect(chart.classList.contains("thermal")).toBe(true)
  })

  it("renders with temperature-only data", () => {
    render(
      <EnergyConvergenceChart thermoData={{ temperature: MULTI_AXIS_DATA.temperature }} />,
    )
    expect(screen.getByTestId("energy-convergence-chart")).toBeInTheDocument()
  })

  it("renders with pressure-only data", () => {
    render(
      <EnergyConvergenceChart thermoData={{ pressure: MULTI_AXIS_DATA.pressure }} />,
    )
    expect(screen.getByTestId("energy-convergence-chart")).toBeInTheDocument()
  })
})
