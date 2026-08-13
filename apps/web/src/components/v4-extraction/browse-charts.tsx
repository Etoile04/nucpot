/**
 * BrowseCharts -- visualization panel with 3 ECharts tabs.
 *
 * - Temperature-Value scatter plot (x: temp K, y: value, series by phase)
 * - Category pie chart (11 property categories)
 * - Confidence bar chart (high/medium/low counts)
 *
 * Uses echarts-for-react with the nfm-dark theme.
 */

"use client"

import { Tabs } from "antd"
import ReactECharts from "echarts-for-react"
import * as echarts from "echarts"
import { useEffect, useMemo } from "react"
import { nfmDarkTheme, DARK_PALETTE } from "@/lib/echarts-dark-theme"
import { PROPERTY_CATEGORIES, CONFIDENCE_LABELS } from "@/lib/v4-extraction/constants"
import type { V4PropertyResponse } from "@/lib/v4-extraction/types"

// ─── Register dark theme ────────────────────────────────────────────

echarts.registerTheme("nfm-dark", nfmDarkTheme)

// ─── Props ──────────────────────────────────────────────────────────

interface BrowseChartsProps {
  properties: V4PropertyResponse[]
}

// ─── Helper: extract numeric temperature from conditions ─────────────

function extractTemperature(conditions?: Record<string, unknown>): number | null {
  if (!conditions) return null

  const temp = conditions["temperature"] ?? conditions["Temperature"] ?? conditions["temp"]
  if (typeof temp === "number") return temp
  if (typeof temp === "string") {
    const parsed = parseFloat(temp)
    return Number.isNaN(parsed) ? null : parsed
  }
  return null
}

// ─── Helper: extract numeric value ──────────────────────────────────

function extractNumericValue(value: string): number | null {
  if (!value) return null
  const cleaned = value.replace(/[^0-9.\-eE]/g, "")
  const parsed = parseFloat(cleaned)
  return Number.isNaN(parsed) ? null : parsed
}

// ─── Component ─────────────────────────────────────────────────────

export default function BrowseCharts({ properties }: BrowseChartsProps) {
  // Suppress React hydration mismatch by ensuring theme is registered
  useEffect(() => {
    echarts.registerTheme("nfm-dark", nfmDarkTheme)
  }, [])

  // ── Scatter: Temperature vs Value by Phase ────────────────────────

  const scatterOption = useMemo(() => {
    const phaseMap = new Map<string, { data: number[][] }>()

    for (const prop of properties) {
      const temp = extractTemperature(prop.conditions)
      const val = extractNumericValue(prop.value)
      if (temp === null || val === null) continue

      const phase = prop.phase ?? "unknown"
      if (!phaseMap.has(phase)) {
        phaseMap.set(phase, { data: [] })
      }
      phaseMap.get(phase)!.data.push([temp, val])
    }

    const series: { name: string; type: string; data: number[][]; symbolSize: number }[] = []
    let paletteIndex = 0
    for (const [phase, { data }] of phaseMap) {
      series.push({
        name: phase,
        type: "scatter",
        data,
        symbolSize: 8,
      })
      paletteIndex = (paletteIndex + 1) % DARK_PALETTE.category.length
    }

    return {
      title: {
        text: "温度-属性值散点图 / Temp vs Value",
        left: "center",
        textStyle: { fontSize: 13 },
      },
      tooltip: {
        trigger: "item",
        formatter: (params: { seriesName: string; data: number[] }) => {
          return [
            `<strong>${params.seriesName}</strong><br/>`,
            `温度 / Temp: ${params.data[0]} K<br/>`,
            `数值 / Value: ${params.data[1]}`,
          ].join("")
        },
      },
      legend: {
        top: 28,
        textStyle: { fontSize: 11 },
      },
      grid: {
        top: 60,
        left: 60,
        right: 20,
        bottom: 40,
      },
      xAxis: {
        name: "温度 / Temp (K)",
        nameLocation: "middle",
        nameGap: 30,
        type: "value",
      },
      yAxis: {
        name: "数值 / Value",
        nameLocation: "middle",
        nameGap: 50,
        type: "value",
      },
      series,
    }
  }, [properties])

  // ── Pie: Property Categories ────────────────────────────────────

  const pieOption = useMemo(() => {
    const categoryCount = new Map<string, number>()

    for (const prop of properties) {
      const cat = prop.property_category ?? "other"
      categoryCount.set(cat, (categoryCount.get(cat) ?? 0) + 1)
    }

    const data = PROPERTY_CATEGORIES.map((cat) => ({
      name: cat.label,
      value: categoryCount.get(cat.value) ?? 0,
    })).filter((item) => item.value > 0)

    return {
      title: {
        text: "属性类别分布 / Category Distribution",
        left: "center",
        textStyle: { fontSize: 13 },
      },
      tooltip: {
        trigger: "item",
        formatter: "{b}: {c} ({d}%)",
      },
      legend: {
        orient: "vertical",
        right: 10,
        top: 40,
        textStyle: { fontSize: 11 },
      },
      series: [
        {
          type: "pie",
          radius: ["30%", "55%"],
          center: ["40%", "55%"],
          avoidLabelOverlap: true,
          itemStyle: {
            borderRadius: 4,
            borderColor: DARK_PALETTE.background,
            borderWidth: 2,
          },
          label: {
            show: false,
          },
          emphasis: {
            label: {
              show: true,
              fontSize: 12,
              fontWeight: "bold",
            },
          },
          data,
        },
      ],
    }
  }, [properties])

  // ── Bar: Confidence Distribution ──────────────────────────────────

  const barOption = useMemo(() => {
    const counts = { high: 0, medium: 0, low: 0 }

    for (const prop of properties) {
      counts[prop.confidence]++
    }

    return {
      title: {
        text: "置信度分布 / Confidence Distribution",
        left: "center",
        textStyle: { fontSize: 13 },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
      },
      grid: {
        top: 50,
        left: 60,
        right: 20,
        bottom: 40,
      },
      xAxis: {
        type: "category",
        data: [CONFIDENCE_LABELS.high, CONFIDENCE_LABELS.medium, CONFIDENCE_LABELS.low],
        axisLabel: { fontSize: 11 },
      },
      yAxis: {
        type: "value",
        minInterval: 1,
      },
      series: [
        {
          type: "bar",
          data: [
            {
              value: counts.high,
              itemStyle: { color: DARK_PALETTE.success },
            },
            {
              value: counts.medium,
              itemStyle: { color: DARK_PALETTE.warning },
            },
            {
              value: counts.low,
              itemStyle: { color: DARK_PALETTE.error },
            },
          ],
          barWidth: "50%",
        },
      ],
    }
  }, [properties])

  // ── Empty state ───────────────────────────────────────────────────

  const isEmpty = properties.length === 0

  if (isEmpty) {
    return (
      <div
        style={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "rgba(0,0,0,0.25)",
        }}
      >
        暂无数据 / No data
      </div>
    )
  }

  // ── Render ───────────────────────────────────────────────────────

  const tabItems = [
    {
      key: "scatter",
      label: "温度散点 / Temp Scatter",
      children: (
        <ReactECharts
          option={scatterOption}
          theme="nfm-dark"
          style={{ height: 320 }}
          notMerge
          lazyUpdate
        />
      ),
    },
    {
      key: "pie",
      label: "类别饼图 / Categories",
      children: (
        <ReactECharts
          option={pieOption}
          theme="nfm-dark"
          style={{ height: 320 }}
          notMerge
          lazyUpdate
        />
      ),
    },
    {
      key: "bar",
      label: "置信度柱状 / Confidence",
      children: (
        <ReactECharts
          option={barOption}
          theme="nfm-dark"
          style={{ height: 320 }}
          notMerge
          lazyUpdate
        />
      ),
    },
  ]

  return (
    <Tabs
      defaultActiveKey="scatter"
      size="small"
      items={tabItems}
      style={{ height: "100%" }}
    />
  )
}
