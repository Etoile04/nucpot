import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { ArcDpaScatterPlot } from "@/components/md-verification/charts/arc-dpa-scatter-plot"

const MOCK_SCATTER = [
  { arc: 0.1, dpa: 0.01 },
  { arc: 0.5, dpa: 0.05 },
  { arc: 1.0, dpa: 0.12 },
  { arc: 2.0, dpa: 0.35 },
  { arc: 5.0, dpa: 0.88 },
]

const MOCK_FIT_LINE = { slope: 0.18, intercept: -0.005 }

const MOCK_CONFIDENCE_BAND = {
  upper: [
    { arc: 0.1, dpa: 0.015 },
    { arc: 1.0, dpa: 0.14 },
    { arc: 5.0, dpa: 0.92 },
  ],
  lower: [
    { arc: 0.1, dpa: 0.005 },
    { arc: 1.0, dpa: 0.10 },
    { arc: 5.0, dpa: 0.84 },
  ],
}

describe("ArcDpaScatterPlot", () => {
  it("renders nothing when scatter data is empty", () => {
    const { container } = render(<ArcDpaScatterPlot scatterData={[]} />)
    expect(container.innerHTML).toBe("")
  })

  it("renders the chart container with data-testid", () => {
    render(<ArcDpaScatterPlot scatterData={MOCK_SCATTER} />)
    const chart = screen.getByTestId("arc-dpa-scatter-plot")
    expect(chart).toBeInTheDocument()
  })

  it("applies custom height and className", () => {
    render(
      <ArcDpaScatterPlot
        scatterData={MOCK_SCATTER}
        height={500}
        className="my-chart"
      />,
    )
    const chart = screen.getByTestId("arc-dpa-scatter-plot")
    expect(chart.style.height).toBe("500px")
    expect(chart.classList.contains("my-chart")).toBe(true)
  })

  it("uses default height of 400px", () => {
    render(<ArcDpaScatterPlot scatterData={MOCK_SCATTER} />)
    const chart = screen.getByTestId("arc-dpa-scatter-plot")
    expect(chart.style.height).toBe("400px")
  })

  it("renders with fit line and confidence band", () => {
    render(
      <ArcDpaScatterPlot
        scatterData={MOCK_SCATTER}
        fitLine={MOCK_FIT_LINE}
        confidenceBand={MOCK_CONFIDENCE_BAND}
      />,
    )
    expect(screen.getByTestId("arc-dpa-scatter-plot")).toBeInTheDocument()
  })

  it("renders with only fit line (no confidence band)", () => {
    render(
      <ArcDpaScatterPlot scatterData={MOCK_SCATTER} fitLine={MOCK_FIT_LINE} />,
    )
    expect(screen.getByTestId("arc-dpa-scatter-plot")).toBeInTheDocument()
  })

  it("renders with single data point", () => {
    render(<ArcDpaScatterPlot scatterData={[MOCK_SCATTER[0]]} />)
    expect(screen.getByTestId("arc-dpa-scatter-plot")).toBeInTheDocument()
  })
})
