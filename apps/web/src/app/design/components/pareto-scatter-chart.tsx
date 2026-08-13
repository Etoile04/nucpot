"use client"

import ReactECharts from "echarts-for-react"
import type { EChartsOption } from "echarts"
import { useMemo } from "react"
import type { ParetoSolution, ObjectiveKey, ConfigType } from "../types"
import {
  ALL_CONFIG_TYPES,
  CONFIG_TYPE_CHART_COLORS,
  CONFIG_TYPE_LABELS,
  OBJECTIVE_META,
} from "../constants"
import { DARK_PALETTE } from "@/lib/echarts-dark-theme"

interface ParetoScatterChartProps {
  data: ParetoSolution[]
  xAxis: ObjectiveKey
  yAxis: ObjectiveKey
  selectedId: string | null
  configTypeFilter: ConfigType[]
  onPointClick: (solution: ParetoSolution) => void
  height?: number | string
}

function getValue(solution: ParetoSolution, key: ObjectiveKey): number {
  switch (key) {
    case "u_density":
      return solution.uDensity
    case "phase_stability":
      return solution.phaseStability
    case "fabricability":
      return solution.fabricability
  }
}

function computeTop3(
  data: ParetoSolution[],
  key: ObjectiveKey,
): ParetoSolution[] {
  return data.reduce<ParetoSolution[]>((best, current) => {
    if (best.length < 3) {
      return [...best, current]
    }
    const worst = best.reduce((min, s) =>
      getValue(s, key) < getValue(min, key) ? s : min,
    )
    if (getValue(current, key) > getValue(worst, key)) {
      return best.map((s) => (s === worst ? current : s))
    }
    return best
  }, [])
}

function formatTooltip(params: any, data: ParetoSolution[], xKey: ObjectiveKey, yKey: ObjectiveKey): string {
  const raw = params?.value
  if (!raw || !Array.isArray(raw)) return ""
  const point = raw[3] ? data.find((s) => s.id === raw[3]) : null
  if (!point) return ""
  const xMeta = OBJECTIVE_META[xKey]
  const yMeta = OBJECTIVE_META[yKey]
  const color = CONFIG_TYPE_CHART_COLORS[point.configType]
  const isTopX = computeTop3(data, xKey).some((s) => s.id === point.id)
  const isTopY = computeTop3(data, yKey).some((s) => s.id === point.id)
  const badges = [
    isTopX ? '<span style="color:#60a5fa">TOP-3 X</span>' : "",
    isTopY ? '<span style="color:#34d399">TOP-3 Y</span>' : "",
  ].filter(Boolean).join(" ")

  return `<div style="margin-bottom:4px"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:6px"></span><strong>${CONFIG_TYPE_LABELS[point.configType]}</strong> <span style="color:#9ca3af">${point.id}</span>${badges ? ` ${badges}` : ""}</div>` +
    `<div style="font-family:monospace;font-size:13px;margin-bottom:4px">${point.composition}</div>` +
    `<div>${xMeta.zh}: <span style="font-family:monospace">${point.uDensity.toFixed(3)} ${xMeta.unit}</span><br/>${yMeta.zh}: <span style="font-family:monospace">${point.phaseStability.toFixed(1)} ${yMeta.unit}</span><br/>可制备性: <span style="font-family:monospace">${point.fabricability.toFixed(3)}</span></div>`
}

/**
 * ParetoScatterChart — 2D scatter chart with color encoding by config type,
 * size by fabricability, selected point highlighting, and TOP-3 pin annotations.
 */
export function ParetoScatterChart({
  data,
  xAxis,
  yAxis,
  selectedId,
  configTypeFilter,
  onPointClick,
  height = 420,
}: ParetoScatterChartProps) {
  const option = useMemo((): EChartsOption => {
    const xMeta = OBJECTIVE_META[xAxis]
    const yMeta = OBJECTIVE_META[yAxis]
    const fabricabilityOnAxis = xAxis === "fabricability" || yAxis === "fabricability"
    const series: EChartsOption["series"] = []

    for (const ct of ALL_CONFIG_TYPES) {
      if (!configTypeFilter.includes(ct)) {
        continue
      }
      const filtered = data.filter((s) => s.configType === ct)
      series.push({
        name: CONFIG_TYPE_LABELS[ct],
        type: "scatter",
        data: filtered.map((s) => [
          getValue(s, xAxis),
          getValue(s, yAxis),
          Math.max(6, Math.min(24, 6 + s.fabricability * 18)),
          s.id,
        ] as [number, number, number, string]),
        itemStyle: { color: CONFIG_TYPE_CHART_COLORS[ct], opacity: 0.8 },
        symbolSize: (value: number[]) =>
          fabricabilityOnAxis ? 10 : (value[2] ?? 10),
        z: 5,
      })
    }

    // Selected point highlight
    const selected = selectedId
      ? data.find((s) => s.id === selectedId)
      : null
    if (selected) {
      series.push({
        name: "Selected",
        type: "scatter",
        data: [[getValue(selected, xAxis), getValue(selected, yAxis)]],
        itemStyle: {
          color: "#facc15",
          borderColor: "#fff",
          borderWidth: 2,
          shadowBlur: 12,
          shadowColor: "rgba(250, 204, 21, 0.6)",
        },
        symbolSize: 14,
        z: 10,
        silent: true,
      })
    }

    // TOP-3 annotations
    const buildTop3Points = (key: ObjectiveKey) => {
      if (!data.length) {
        return undefined
      }
      const top3 = computeTop3(data, key)
      const meta = OBJECTIVE_META[key]
      return top3.map((s) => ({
        name: s.id,
        coord: [getValue(s, xAxis), getValue(s, yAxis)] as [number, number],
        symbol: "pin",
        symbolSize: 32,
        label: {
          formatter: `${meta.zh}\n${meta.en}`,
          fontSize: 10,
          fontWeight: "bold" as const,
          color: "#fff",
        },
        itemStyle: { color: DARK_PALETTE.accent },
      }))
    }

    const markPoints = [
      ...(buildTop3Points(xAxis) ?? []),
      ...(buildTop3Points(yAxis) ?? []),
    ]

    if (markPoints.length > 0) {
      series.push({
        name: "__annotations__",
        type: "scatter",
        data: [],
        markPoint: { data: markPoints, animation: false },
        symbolSize: 0,
        silent: true,
        z: 8,
      })
    }

    return {
      tooltip: {
        trigger: "item",
        formatter: (params: any) =>
          formatTooltip(params, data, xAxis, yAxis),
      },
      legend: {
        bottom: 0,
        textStyle: { color: DARK_PALETTE.textSecondary, fontSize: 12 },
        data: ALL_CONFIG_TYPES
          .filter((ct) => configTypeFilter.includes(ct))
          .map((ct) => CONFIG_TYPE_LABELS[ct]),
      },
      toolbox: {
        right: 16,
        top: 8,
        feature: { dataZoom: {}, restore: {} },
      },
      grid: {
        left: "3%",
        right: "6%",
        bottom: "12%",
        top: "6%",
        containLabel: true,
      },
      xAxis: {
        type: "value",
        name: `${xMeta.zh} / ${xMeta.en} (${xMeta.unit})`,
        nameLocation: "middle",
        nameGap: 32,
      },
      yAxis: {
        type: "value",
        name: `${yMeta.zh} / ${yMeta.en} (${yMeta.unit})`,
        nameLocation: "middle",
        nameGap: 48,
      },
      dataZoom: [
        { type: "inside", xAxisIndex: 0 },
        { type: "inside", yAxisIndex: 0 },
        { type: "slider", xAxisIndex: 0, bottom: 36, height: 20 },
      ],
      series,
    }
  }, [data, xAxis, yAxis, selectedId, configTypeFilter])

  const handleEvents = useMemo(
    () => ({
      click: (params: any) => {
        const raw = params?.value
        if (raw && Array.isArray(raw) && raw[3]) {
          const point = data.find((s) => s.id === raw[3])
          if (point) {
            onPointClick(point)
          }
        }
      },
    }),
    [data, onPointClick],
  )

  return (
    <div style={{ width: "100%", height }}>
      <ReactECharts
        option={option}
        theme="nfm-dark"
        style={{ width: "100%", height: "100%" }}
        notMerge
        lazyUpdate
        onEvents={handleEvents}
      />
    </div>
  )
}
