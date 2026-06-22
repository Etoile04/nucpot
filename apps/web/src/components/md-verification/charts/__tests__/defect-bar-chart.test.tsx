import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { DefectBarChart } from "@/components/md-verification/charts/defect-bar-chart"
import type { DefectAnalysisResultResponse } from "@/lib/md-verification-api"
import { DefectType } from "@/lib/md-verification-api"

const MOCK_DEFECT_DATA: DefectAnalysisResultResponse[] = [
  {
    id: "defect-1",
    verification_job_id: "job-1",
    defect_type: DefectType.VACANCY,
    concentration: 0.0012,
    formation_energy: -3.45,
    metadata: null,
  },
  {
    id: "defect-2",
    verification_job_id: "job-1",
    defect_type: DefectType.INTERSTITIAL,
    concentration: 0.0008,
    formation_energy: -2.10,
    metadata: null,
  },
  {
    id: "defect-3",
    verification_job_id: "job-1",
    defect_type: DefectType.DISLOCATION,
    concentration: 0.0003,
    formation_energy: null,
    metadata: null,
  },
]

describe("DefectBarChart", () => {
  it("renders nothing when data is empty", () => {
    const { container } = render(<DefectBarChart data={[]} />)
    expect(container.innerHTML).toBe("")
  })

  it("renders the chart container with data-testid", () => {
    render(<DefectBarChart data={MOCK_DEFECT_DATA} height={300} />)
    const chart = screen.getByTestId("defect-bar-chart")
    expect(chart).toBeInTheDocument()
  })

  it("applies custom height", () => {
    render(<DefectBarChart data={MOCK_DEFECT_DATA} height={450} className="custom-class" />)
    const chart = screen.getByTestId("defect-bar-chart")
    expect(chart).toBeInTheDocument()
    expect(chart.style.height).toBe("450px")
    expect(chart.classList.contains("custom-class")).toBe(true)
  })

  it("uses default height of 300px", () => {
    render(<DefectBarChart data={MOCK_DEFECT_DATA} />)
    const chart = screen.getByTestId("defect-bar-chart")
    expect(chart.style.height).toBe("300px")
  })

  it("renders with single defect type", () => {
    const singleDefect = [MOCK_DEFECT_DATA[0]]
    render(<DefectBarChart data={singleDefect} />)
    expect(screen.getByTestId("defect-bar-chart")).toBeInTheDocument()
  })
})
