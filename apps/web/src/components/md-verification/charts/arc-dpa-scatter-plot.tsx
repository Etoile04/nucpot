"use client"

import ReactECharts from "echarts-for-react"
import type { EChartsOption } from "echarts"
import { useMemo } from "react"
import { nfmDarkTheme, DARK_PALETTE } from "@/lib/echarts-dark-theme"

interface ScatterPoint {
  arc: number
  dpa: number
}

interface FitLine {
  slope: number
  intercept: number
}

interface ConfidenceBand {
  upper: Array<{ arc: number; dpa: number }>
  lower: Array<{ arc: number; dpa: number }>
}

interface ArcDpaScatterPlotProps {
  /** Scatter data points (arc displacement vs DPA) */
  scatterData: ScatterPoint[]
  /** Optional fit line parameters */
  fitLine?: FitLine
  /** Optional 95% confidence band boundaries */
  confidenceBand?: ConfidenceBand
  /** Chart height in pixels. Defaults to 400. */
  height?: number
  /** Optional CSS class for the wrapper div */
  className?: string
}

/**
 * ArcDpaScatterPlot — scatter plot of arc displacement vs DPA,
 * with optional fit line and 95% confidence interval shading.
 *
 * This is the primary scientific chart for MD verification,
 * showing how displacement cascades evolve under irradiation dose.
 */
export function ArcDpaScatterPlot({
  scatterData,
  fitLine,
  confidenceBand,
  height = 400,
  className,
}: ArcDpaScatterPlotProps) {
  const option = useMemo((): EChartsOption => {
    const series: EChartsOption["series"] = []

    // Confidence band shading (rendered first, behind scatter)
    if (confidenceBand && confidenceBand.upper.length > 0) {
      const bandData = [
        ...confidenceBand.upper.map((p) => [p.arc, p.dpa] as [number, number]),
        ...confidenceBand.lower.slice().reverse().map((p) => [p.arc, p.dpa] as [number, number]),
      ]

      series.push({
        name: "95% 置信区间",
        type: "line",
        data: bandData,
        silent: true,
        symbol: "none",
        lineStyle: { opacity: 0 },
        areaStyle: {
          color: DARK_PALETTE.accent,
          opacity: 0.1,
        },
        z: 1,
      })
    }

    // Fit line
    if (fitLine) {
      const xMin = Math.min(...scatterData.map((p) => p.arc))
      const xMax = Math.max(...scatterData.map((p) => p.arc))
      const margin = (xMax - xMin) * 0.05

      series.push({
        name: "拟合线",
        type: "line",
        data: [
          [xMin - margin, fitLine.slope * (xMin - margin) + fitLine.intercept],
          [xMax + margin, fitLine.slope * (xMax + margin) + fitLine.intercept],
        ],
        silent: true,
        symbol: "none",
        lineStyle: {
          color: DARK_PALETTE.warning,
          width: 2,
          type: "dashed",
        },
        z: 2,
      })
    }

    // Scatter points
    series.push({
      name: "arc-dpa 数据点",
      type: "scatter",
      data: scatterData.map((p) => [p.arc, p.dpa] as [number, number]),
      itemStyle: {
        color: DARK_PALETTE.accent,
        opacity: 0.7,
      },
      emphasis: {
        itemStyle: {
          color: DARK_PALETTE.accent,
          opacity: 1,
          borderColor: "#fff",
          borderWidth: 1,
        },
      },
      z: 3,
    })

    return {
      tooltip: {
        trigger: "item",
        formatter: (params: any) => {
          const [arc, dpa] = params?.value ?? []
          return [
            `<strong>${params?.seriesName ?? ""}</strong>`,
            `arc 位移: ${typeof arc === "number" ? arc.toFixed(4) : arc}`,
            `DPA: ${typeof dpa === "number" ? dpa.toExponential(4) : dpa}`,
          ].join("<br/>")
        },
      } as EChartsOption["tooltip"],
      grid: {
        left: "3%",
        right: "4%",
        bottom: "3%",
        top: "8%",
        containLabel: true,
      },
      xAxis: {
        type: "value",
        name: "arc 位移",
        nameLocation: "middle",
        nameGap: 28,
      },
      yAxis: {
        type: "value",
        name: "DPA",
        nameLocation: "middle",
        nameGap: 40,
      },
      series,
    }
  }, [scatterData, fitLine, confidenceBand])

  if (scatterData.length === 0) {
    return null
  }

  return (
    <div
      className={className}
      style={{ width: "100%", height }}
      data-testid="arc-dpa-scatter-plot"
    >
      <ReactECharts
        option={option}
        theme={nfmDarkTheme}
        style={{ width: "100%", height: "100%" }}
        opts={{ renderer: "canvas" }}
      />
    </div>
  )
}
