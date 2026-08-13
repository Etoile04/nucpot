/**
 * ParetoChartContainer accessibility tests — NFM-1703.
 *
 * Verifies that the container's role/aria-label does not suppress child
 * nodes (notably the antd `Result` error component) from the accessibility
 * tree. Regression guard for QA finding W2.
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"

// Mock heavy / chart sub-components so the test focuses on a11y semantics
vi.mock("../components/pareto-scatter-chart", () => ({
  ParetoScatterChart: () => <div data-testid="mock-scatter" />,
}))
vi.mock("../components/convergence-line-chart", () => ({
  ConvergenceLineChart: () => <div data-testid="mock-convergence" />,
}))
vi.mock("../components/axis-switcher", () => ({
  AxisSwitcher: () => <div data-testid="mock-axis" />,
}))
vi.mock("../components/loading-overlay", () => ({
  LoadingOverlay: () => <div data-testid="mock-loading" />,
}))

import { ParetoChartContainer } from "../components/pareto-chart-container"

const baseProps = {
  paretoData: [],
  generationalDistance: [],
  hypervolume: [],
  selectedId: null,
  configTypeFilter: [],
  isOptimizing: false,
  optimizationProgress: 0,
  isLoading: false,
  isError: false,
  optimizationStatus: "idle" as const,
  onPointClick: vi.fn(),
  onRetry: vi.fn(),
  onReset: vi.fn(),
}

describe("ParetoChartContainer accessibility (NFM-1703)", () => {
  it("uses role=region (not role=img) on the outermost container so children appear in the a11y tree", () => {
    const { container } = render(<ParetoChartContainer {...baseProps} />)
    const region = container.querySelector('[role="region"]')
    expect(region).not.toBeNull()
    // Regression guard for NFM-1703: the outer container must NOT claim
    // to be a single accessible image, which would flatten descendants
    // (including the antd Result error component) out of the a11y tree.
    expect(region!.getAttribute('role')).toBe('region')

    // And the outermost wrapper element must not carry role="img" at all.
    // antd's internal decorative icons may use role="img" with aria-hidden,
    // which is unrelated and out of scope for this fix.
    const firstDiv = container.firstElementChild
    expect(firstDiv?.getAttribute('role')).not.toBe('img')
  })

  it("has a descriptive accessible name on the region", () => {
    render(<ParetoChartContainer {...baseProps} />)
    expect(
      screen.getByRole("region", {
        name: /Pareto优化结果|Pareto optimization results/,
      }),
    ).toBeInTheDocument()
  })

  it("exposes the antd Result error component to assistive tech when isError=true", () => {
    const { container } = render(
      <ParetoChartContainer
        {...baseProps}
        isError
        errorMessage="boom"
        onRetry={vi.fn()}
      />,
    )

    // Result renders an element with the .ant-result class.
    // Under role=img this would have been hidden from the a11y tree;
    // under role=region it remains queryable.
    const resultEl = container.querySelector(".ant-result")
    expect(resultEl).not.toBeNull()

    // Error title and retry button are both findable by role
    expect(
      screen.getByRole("button", { name: /重试|Retry/ }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/加载失败|Load Failed/),
    ).toBeInTheDocument()
  })
})