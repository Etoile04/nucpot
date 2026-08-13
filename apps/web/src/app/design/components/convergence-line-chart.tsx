"use client"

import ReactECharts from "echarts-for-react"
import type { EChartsOption } from "echarts"
import { useMemo } from "react"
import type { ConvergencePoint } from "../types"
import { DARK_PALETTE } from "@/lib/echarts-dark-theme"

interface ConvergenceLineChartProps {
  generationalDistance: ConvergencePoint[]
  hypervolume: ConvergencePoint[]
}

/**
 * ConvergenceLineChart — dual-axis line chart for Generational Distance
 * (GD, left Y) and Hypervolume (HV, right Y) over optimization generations.
 */
export function ConvergenceLineChart({
  generationalDistance,
  hypervolume,
}: ConvergenceLineChartProps) {
  const option = useMemo((): EChartsOption => {
    const series: EChartsOption["series"] = [
      {
        name: "GD (↓)",
        type: "line",
        smooth: true,
        symbol: "circle",
        symbolSize: 4,
        lineStyle: { width: 2 },
        color: DARK_PALETTE.error,
        yAxisIndex: 0,
        data: generationalDistance.map(
          (p) => [p.generation, p.value] as [number, number],
        ),
      },
      {
        name: "HV (↑)",
        type: "line",
        smooth: true,
        symbol: "circle",
        symbolSize: 4,
        lineStyle: { width: 2 },
        color: DARK_PALETTE.success,
        yAxisIndex: 1,
        data: hypervolume.map(
          (p) => [p.generation, p.value] as [number, number],
        ),
      },
    ]

    return {
      tooltip: {
        trigger: "axis",
        formatter: (params: any) => {
          if (!Array.isArray(params)) {
            return ""
          }
          const gen = params[0]?.data?.[0]
          const lines = params.map((p: any) => {
            const val = p?.data?.[1]
            return `${p.marker} ${p.seriesName}: ${typeof val === "number" ? val.toFixed(6) : val}`
          })
          return [`Generation ${gen}`, ...lines].join("<br/>")
        },
      },
      legend: {
        bottom: 0,
        data: ["GD (↓)", "HV (↑)"],
        textStyle: {
          color: DARK_PALETTE.textSecondary,
          fontSize: 12,
        },
      },
      grid: {
        left: "3%",
        right: "4%",
        bottom: "12%",
        top: "8%",
        containLabel: true,
      },
      xAxis: {
        type: "value",
        name: "Generation",
        nameLocation: "middle",
        nameGap: 28,
      },
      yAxis: [
        {
          type: "value",
          name: "GD",
          nameLocation: "end",
          position: "left",
        },
        {
          type: "value",
          name: "HV",
          nameLocation: "end",
          position: "right",
        },
      ],
      series,
    }
  }, [generationalDistance, hypervolume])

  return (
    <div style={{ width: "100%", height: 300 }}>
      <ReactECharts
        option={option}
        theme="nfm-dark"
        style={{ width: "100%", height: "100%" }}
        notMerge
        lazyUpdate
      />
    </div>
  )
}
