"use client"

import ReactECharts from "echarts-for-react"
import type { EChartsOption } from "echarts"
import { useMemo } from "react"
import { nfmDarkTheme, DARK_PALETTE } from "@/lib/echarts-dark-theme"
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
 * Per-bar colors aligned with UX spec Section 3.3.
 * vacancies=blue, interstitials=green, Frenkel-pairs-style=amber,
 * dislocation=red, grain_boundary=violet, other=gray.
 */
const DEFECT_COLORS: Record<string, string> = {
  vacancy: DARK_PALETTE.accent,      // blue-400
  interstitial: DARK_PALETTE.success, // emerald-400
  dislocation: DARK_PALETTE.error,   // red-400
  grain_boundary: DARK_PALETTE.warning, // amber-400
  other: "#6b7280",                    // gray-500
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
  const option = useMemo((): EChartsOption => {
    if (data.length === 0) {
      return {}
    }

    const categories = data.map((d) => DEFECT_LABELS[d.defect_type] ?? d.defect_type)
    const concentrations = data.map((d) => d.concentration)

    return {
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params: any) => {
          const item = Array.isArray(params) ? params[0] : params
          if (!item) return ""
          const result = data[item.dataIndex]
          const lines = [
            `<strong>${item.name}</strong>`,
            `浓度: ${(item.value as number).toExponential(4)}`,
          ]
          if (result && result.formation_energy !== null) {
            lines.push(`形成能: ${result.formation_energy.toFixed(4)} eV`)
          }
          return lines.join("<br/>")
        },
      } as EChartsOption["tooltip"],
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
          data: data.map((d, idx) => ({
            value: concentrations[idx] ?? 0,
            itemStyle: {
              color: DEFECT_COLORS[d.defect_type] ?? "#6b7280",
              borderRadius: [4, 4, 0, 0],
            },
          })),
          barMaxWidth: 60,
          label: {
            show: true,
            position: "top" as const,
            formatter: (params: any) => (params.value as number).toExponential(2),
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
