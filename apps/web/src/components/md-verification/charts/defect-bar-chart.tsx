"use client"

import ReactECharts from "echarts-for-react"
import type { EChartsOption } from "echarts"
import { useMemo } from "react"
import { nfmDarkTheme } from "@/lib/echarts-dark-theme"
import type { DefectAnalysisResultResponse } from "@/lib/md-verification-api"

interface DefectBarChartProps {
  /** Defect analysis results to visualize */
  data: DefectAnalysisResultResponse[]
  /** Chart height in pixels. Defaults to 300. */
  height?: number
  /** Optional CSS class for the wrapper div */
  className?: string
}

/** Defect type labels for Chinese display */
const DEFECT_LABELS: Record<string, string> = {
  vacancy: "空位",
  interstitial: "间隙原子",
  dislocation: "位错",
  grain_boundary: "晶界",
  other: "其他",
}

/**
 * DefectBarChart — bar chart showing defect concentrations by type.
 *
 * Each bar represents one defect type with its concentration and
 * optional formation energy annotation.
 */
export function DefectBarChart({
  data,
  height = 300,
  className,
}: DefectBarChartProps) {
  const option: EChartsOption = useMemo(() => {
    if (data.length === 0) {
      return {}
    }

    const categories = data.map((d) => DEFECT_LABELS[d.defect_type] ?? d.defect_type)
    const concentrations = data.map((d) => d.concentration)

    return {
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params: Array<{ name: string; value: number; dataIndex: number }>) => {
          const item = params[0]
          if (!item) return ""
          const result = data[item.dataIndex]
          const lines = [
            `<strong>${item.name}</strong>`,
            `浓度: ${item.value.toExponential(4)}`,
          ]
          if (result.formation_energy !== null) {
            lines.push(`形成能: ${result.formation_energy.toFixed(4)} eV`)
          }
          return lines.join("<br/>")
        },
      },
      grid: {
        left: "3%",
        right: "4%",
        bottom: "3%",
        top: "12%",
        containLabel: true,
      },
      xAxis: {
        type: "category",
        data: categories,
        axisLabel: {
          rotate: categories.length > 4 ? 30 : 0,
        },
      },
      yAxis: {
        type: "value",
        name: "浓度",
        axisLabel: {
          formatter: (value: number) => value.toExponential(1),
        },
      },
      series: [
        {
          name: "缺陷浓度",
          type: "bar",
          data: concentrations,
          barMaxWidth: 60,
          itemStyle: {
            borderRadius: [4, 4, 0, 0],
          },
          label: {
            show: true,
            position: "top",
            formatter: (params: { value: number }) => params.value.toExponential(2),
            fontSize: 11,
          },
        },
      ],
    }
  }, [data])

  if (data.length === 0) {
    return null
  }

  return (
    <div
      className={className}
      style={{ width: "100%", height }}
      data-testid="defect-bar-chart"
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
