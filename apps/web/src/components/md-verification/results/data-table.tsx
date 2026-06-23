"use client"

import { useMemo } from "react"
import { Card, Table, Tag, Typography } from "antd"
import type { ColumnsType } from "antd/es/table"
import VerificationBadge from "@/components/VerificationBadge"
import type { DefectAnalysisResultResponse } from "@/lib/md-verification-api"

const { Text } = Typography

/** Defect type labels for Chinese display */
const DEFECT_LABELS: Record<string, string> = {
  vacancy: "空位",
  interstitial: "间隙原子",
  dislocation: "位错",
  grain_boundary: "晶界",
  other: "其他",
}

interface DataRow {
  key: string
  defectType: string
  concentration: number
  formationEnergy: number | null
  grade: string | null
  passed: boolean
}

interface ResultsDataTableProps {
  /** Defect analysis results to display */
  data: DefectAnalysisResultResponse[]
  /** Optional overall grade for display */
  overallGrade?: string | null
  /** Optional CSS class */
  className?: string
}

/**
 * Determine a simple grade from formation energy quality.
 * This is a placeholder heuristic — production grades come from the API.
 */
function computeGrade(energy: number | null): string | null {
  if (energy === null) return null
  const abs = Math.abs(energy)
  if (abs < 1.0) return "A"
  if (abs < 3.0) return "B"
  if (abs < 5.0) return "C"
  if (abs < 7.0) return "D"
  return "F"
}

function isPassed(grade: string | null): boolean {
  if (!grade) return false
  return ["A", "B", "C"].includes(grade.toUpperCase())
}

/**
 * ResultsDataTable — detailed numerical data table per UX spec Section 3.1.
 *
 * Columns: 缺陷类型, 浓度, 形成能 (eV), 评级, 状态
 */
export function ResultsDataTable({
  data,
  overallGrade,
  className,
}: ResultsDataTableProps) {
  const rows: DataRow[] = useMemo(
    () =>
      data.map((d) => ({
        key: d.id,
        defectType: DEFECT_LABELS[d.defect_type] ?? d.defect_type,
        concentration: d.concentration,
        formationEnergy: d.formation_energy,
        grade: d.metadata
          ? (d.metadata as Record<string, unknown>).grade as string | null ?? null
          : computeGrade(d.formation_energy),
        passed: isPassed(
          d.metadata
            ? (d.metadata as Record<string, unknown>).grade as string | null ?? null
            : computeGrade(d.formation_energy),
        ),
      })),
    [data],
  )

  const columns: ColumnsType<DataRow> = [
    {
      title: "缺陷类型",
      dataIndex: "defectType",
      key: "defectType",
      sorter: (a, b) => a.defectType.localeCompare(b.defectType),
    },
    {
      title: "浓度",
      dataIndex: "concentration",
      key: "concentration",
      sorter: (a, b) => a.concentration - b.concentration,
      render: (val: number) => (
        <Text code>
          {val.toExponential(4)}
        </Text>
      ),
    },
    {
      title: "形成能 (eV)",
      dataIndex: "formationEnergy",
      key: "formationEnergy",
      sorter: (a, b) => (a.formationEnergy ?? 0) - (b.formationEnergy ?? 0),
      render: (val: number | null) =>
        val !== null ? (
          <Text>{val.toFixed(4)}</Text>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "评级",
      dataIndex: "grade",
      key: "grade",
      width: 100,
      render: (grade: string | null) => (
        <VerificationBadge grade={grade} size="sm" />
      ),
    },
    {
      title: "状态",
      dataIndex: "passed",
      key: "passed",
      width: 80,
      render: (passed: boolean) => (
        <Tag color={passed ? "success" : "error"}>
          {passed ? "达标" : "未达标"}
        </Tag>
      ),
    },
  ]

  return (
    <Card
      title={
        <span>
          详细数据
          {overallGrade && (
            <VerificationBadge
              grade={overallGrade}
              size="sm"
              style={{ marginLeft: 8 }}
            />
          )}
        </span>
      }
      size="small"
      className={className}
      data-testid="results-data-table"
    >
      <Table
        columns={columns}
        dataSource={rows}
        size="small"
        pagination={false}
        scroll={{ x: 600 }}
      />
    </Card>
  )
}
