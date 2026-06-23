"use client"

import ReactECharts from "echarts-for-react"
import type { EChartsOption } from "echarts"
import { useMemo } from "react"
import { nfmDarkTheme, DARK_PALETTE } from "@/lib/echarts-dark-theme"

interface EnergyDataPoint {
  step: number
  energy: number
}

interface TemperatureDataPoint {
  step: number
  temperature: number
}

interface PressureDataPoint {
  step: number
  pressure: number
}

interface ThermodynamicData {
  energy?: EnergyDataPoint[]
  temperature?: TemperatureDataPoint[]
  pressure?: PressureDataPoint[]
}

interface EnergyConvergenceChartProps {
  /** Thermodynamic data from simulation results */
  thermoData: ThermodynamicData
  /** Chart height in pixels. Defaults to 300. */
  height?: number
  /** Optional CSS class for the wrapper div */
  className?: string
}

/**
 * EnergyConvergenceChart — line chart showing energy convergence over simulation steps.
 * Optionally overlays temperature and/or pressure as secondary y-axes.
 */
export function EnergyConvergenceChart({
  thermoData,
  height = 300,
  className,
}: EnergyConvergenceChartProps) {
  const option = useMemo((): EChartsOption => {
    const series: EChartsOption["series"] = []
    const yAxisIndexMap = new Map<string, number>()
    let yAxisIndex = 0

    // Primary axis: Energy
    if (thermoData.energy && thermoData.energy.length > 0) {
      yAxisIndexMap.set("energy", 0)

      series.push({
        name: "能量 (eV)",
        type: "line",
        data: thermoData.energy.map((p) => [p.step, p.energy]),
        smooth: true,
        symbolSize: 2,
        lineStyle: { width: 2 },
        yAxisIndex: 0,
      })

      yAxisIndex = 1
    }

    // Secondary axis: Temperature
    if (thermoData.temperature && thermoData.temperature.length > 0) {
      const hasEnergy = yAxisIndexMap.has("energy")
      const tempYIndex = hasEnergy ? 1 : 0
      yAxisIndexMap.set("temperature", tempYIndex)

      series.push({
        name: "温度 (K)",
        type: "line",
        data: thermoData.temperature.map((p) => [p.step, p.temperature]),
        smooth: false,
        symbolSize: 2,
        lineStyle: { width: 1, type: "dotted" },
        itemStyle: { color: DARK_PALETTE.error },
        yAxisIndex: tempYIndex,
      })

      if (hasEnergy) {
        yAxisIndex = 2
      }
    }

    // Secondary axis: Pressure
    if (thermoData.pressure && thermoData.pressure.length > 0) {
      const pressureYIndex = yAxisIndex
      yAxisIndexMap.set("pressure", pressureYIndex)

      series.push({
        name: "压力 (GPa)",
        type: "line",
        data: thermoData.pressure.map((p) => [p.step, p.pressure]),
        smooth: false,
        symbolSize: 2,
        lineStyle: { width: 1, type: "dotted" },
        itemStyle: { color: DARK_PALETTE.success },
        yAxisIndex: pressureYIndex,
      })
    }

    const yAxes: EChartsOption["yAxis"] = []

    if (yAxisIndexMap.has("energy")) {
      yAxes.push({
        type: "value",
        name: "能量 (eV)",
        position: "left",
        nameLocation: "middle",
        nameGap: 50,
        axisLabel: {
          formatter: (value: number) => value.toFixed(2),
        },
      })
    }

    if (yAxisIndexMap.has("temperature")) {
      yAxes.push({
        type: "value",
        name: "温度 (K)",
        position: yAxisIndexMap.has("pressure") ? "left" : "right",
        nameLocation: "middle",
        nameGap: 50,
        axisLabel: {
          formatter: (value: number) => value.toFixed(0),
        },
        splitLine: { show: false },
      })
    }

    if (yAxisIndexMap.has("pressure")) {
      yAxes.push({
        type: "value",
        name: "压力 (GPa)",
        position: "right",
        nameLocation: "middle",
        nameGap: 50,
        axisLabel: {
          formatter: (value: number) => value.toFixed(2),
        },
        splitLine: { show: false },
      })
    }

    return {
      tooltip: {
        trigger: "axis",
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (params: any) => {
          const step = params[0]?.axisValue ?? ""
          const lines = [`<strong>步数: ${step}</strong>`]
          for (const p of params) {
            const val = p.value?.[1]
            if (val !== undefined) {
              lines.push(`${p.seriesName}: ${typeof val === "number" ? val.toFixed(4) : val}`)
            }
          }
          return lines.join("<br/>")
        },
      } as EChartsOption["tooltip"],
      legend: {
        data: series.map((s) => (s as { name?: string }).name).filter((n): n is string => n !== undefined),
        bottom: 0,
      },
      grid: {
        left: "3%",
        right: yAxisIndexMap.size > 2 ? "7%" : "4%",
        bottom: "12%",
        top: "8%",
        containLabel: true,
      },
      xAxis: {
        type: "value",
        name: "步数",
        nameLocation: "middle",
        nameGap: 28,
      },
      yAxis: yAxes,
      series,
    }
  }, [thermoData])

  const hasData =
    (thermoData.energy?.length ?? 0) > 0 ||
    (thermoData.temperature?.length ?? 0) > 0 ||
    (thermoData.pressure?.length ?? 0) > 0

  if (!hasData) {
    return null
  }

  return (
    <div
      className={className}
      style={{ width: "100%", height }}
      data-testid="energy-convergence-chart"
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
