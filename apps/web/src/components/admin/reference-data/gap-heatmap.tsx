/** Gap heatmap visualization component. */

"use client"

import { Card, Spin, Table, Tooltip, message, Modal } from "antd"
import type { ColumnsType } from "antd/es/table"
import { useState, useMemo } from "react"
import type { SystemCoverageBreakdown } from "@/lib/reference-gaps/types"
import { fillGap } from "@/lib/reference-gaps/api"

interface GapHeatmapProps {
  data: SystemCoverageBreakdown[]
  loading?: boolean
  onFillSuccess?: () => void
}

interface HeatmapCell {
  key: string
  element_system: string
  phase: string | null
  property_name: string
  status: "covered" | "gap" | "pending"
  count?: number
}

// Property names based on target schema (hardcoded per NFM-73 spec)
const PROPERTY_NAMES = [
  "density",
  "melting_point",
  "thermal_conductivity",
  "youngs_modulus",
  "yield_strength",
]

export function GapHeatmap({ data, loading, onFillSuccess }: GapHeatmapProps) {
  const [filling, setFilling] = useState(false)

  // Transform breakdown data into heatmap cells
  const heatmapCells = useMemo<HeatmapCell[]>(() => {
    const cells: HeatmapCell[] = []
    const coverageMap = new Map<string, SystemCoverageBreakdown>()

    // Build coverage lookup map
    data.forEach((item) => {
      const key = `${item.element_system}|${item.phase || "default"}`
      coverageMap.set(key, item)
    })

    // Generate cells for each system/phase x property combination
    data.forEach((item) => {
      const key = `${item.element_system}|${item.phase || "default"}`
      const coverage = coverageMap.get(key)!

      PROPERTY_NAMES.forEach((prop) => {
        // Simplified logic: determine status based on gaps count
        // In production, this would check actual gap data
        const hasGap = coverage.gaps > 0
        const status: HeatmapCell["status"] = hasGap
          ? "gap"
          : coverage.covered > 0
            ? "covered"
            : "pending"

        cells.push({
          key: `${key}|${prop}`,
          element_system: item.element_system,
          phase: item.phase,
          property_name: prop,
          status,
          count: hasGap ? coverage.gaps : coverage.covered,
        })
      })
    })

    return cells
  }, [data])

  // Group cells by system/phase for table rows
  const tableData = useMemo(() => {
    const groups = new Map<string, HeatmapCell[]>()

    heatmapCells.forEach((cell) => {
      const rowKey = `${cell.element_system}|${cell.phase || "default"}`
      if (!groups.has(rowKey)) {
        groups.set(rowKey, [])
      }
      groups.get(rowKey)!.push(cell)
    })

    return Array.from(groups.entries()).map(([key, cells]) => {
      const [element_system, phase] = key.split("|")
      return {
        key,
        element_system,
        phase: phase === "default" ? null : phase,
        cells,
      }
    })
  }, [heatmapCells])

  const handleCellClick = async (cell: HeatmapCell) => {
    if (cell.status !== "gap") {
      return
    }

    Modal.confirm({
      title: "确认填充缺口",
      content: `确定要填充 ${cell.element_system} / ${cell.phase || "默认"} / ${cell.property_name} 的缺口吗？`,
      okText: "确认",
      cancelText: "取消",
      onOk: async () => {
        setFilling(true)
        try {
          await fillGap({
            element_system: cell.element_system,
            phase: cell.phase || undefined,
            property_name: cell.property_name,
          })
          message.success("缺口填充成功")
          onFillSuccess?.()
        } catch (error) {
          message.error(`填充失败: ${error instanceof Error ? error.message : "未知错误"}`)
        } finally {
          setFilling(false)
        }
      },
    })
  }

  const renderCell = (cell: HeatmapCell) => {
    const colorMap = {
      covered: "#52c41a",
      gap: "#ff4d4f",
      pending: "#faad14",
    }

    const statusText = {
      covered: "已覆盖",
      gap: "缺口",
      pending: "待暂存",
    }

    return (
      <Tooltip
        title={`${cell.element_system} / ${cell.phase || "默认"} / ${cell.property_name}: ${statusText[cell.status]} (${cell.count || 0})`}
      >
        <div
          onClick={() => handleCellClick(cell)}
          style={{
            backgroundColor: colorMap[cell.status],
            color: "white",
            padding: "8px",
            textAlign: "center",
            cursor: cell.status === "gap" ? "pointer" : "default",
            borderRadius: "4px",
            fontWeight: cell.status === "gap" ? "bold" : "normal",
            transition: "all 0.2s",
          }}
          onMouseEnter={(e) => {
            if (cell.status === "gap") {
              e.currentTarget.style.transform = "scale(1.05)"
            }
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = "scale(1)"
          }}
        >
          {cell.count || 0}
        </div>
      </Tooltip>
    )
  }

  const columns: ColumnsType<(typeof tableData)[0]> = [
    {
      title: "元素系统",
      dataIndex: "element_system",
      key: "element_system",
      fixed: "left",
      width: 150,
    },
    {
      title: "阶段",
      dataIndex: "phase",
      key: "phase",
      width: 100,
      render: (phase: string | null) => phase || "默认",
    },
    ...PROPERTY_NAMES.map((prop) => ({
      title: prop,
      dataIndex: "cells",
      key: prop,
      width: 120,
      render: (cells: HeatmapCell[]) => {
        const cell = cells.find((c) => c.property_name === prop)
        return cell ? renderCell(cell) : <span>-</span>
      },
    })),
  ]

  return (
    <Card title="缺口热力图" loading={loading}>
      <Spin spinning={filling}>
        <Table
          columns={columns}
          dataSource={tableData}
          pagination={false}
          scroll={{ x: "max-content" }}
          size="small"
        />
      </Spin>
    </Card>
  )
}
