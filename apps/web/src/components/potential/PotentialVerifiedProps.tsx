"use client"

import { Table, Tag, Empty, Typography } from "antd"
import type { ColumnsType } from "antd/es/table"
import type { PotentialDetail } from "@/lib/potentials-api"

const { Text } = Typography

const PROPERTY_LABELS: Record<string, string> = {
  lattice_constant: "晶格常数",
  lattice_constants: "晶格常数",
  elastic_constants: "弹性常数",
  c11: "C₁₁",
  c12: "C₁₂",
  c44: "C₄₄",
  bulk_modulus: "体积模量",
  shear_modulus: "剪切模量",
  youngs_modulus: "杨氏模量",
  poisson_ratio: "泊松比",
  formation_energy: "形成能",
  cohesive_energy: "结合能",
  melting_point: "熔点",
  surface_energy: "表面能",
  stacking_fault_energy: "层错能",
  vacancy_formation_energy: "空位形成能",
  interstitial_formation_energy: "间隙形成能",
  thermal_expansion: "热膨胀系数",
  specific_heat: "比热容",
  thermal_conductivity: "热导率",
  density: "密度",
}

function getLabel(key: string): string {
  return PROPERTY_LABELS[key] ?? key
}

function extractNumeric(v: unknown): number | null {
  if (typeof v === "number") return v
  if (typeof v === "string") {
    const m = v.match(/-?[\d.]+(?:e[+-]?\d+)?/i)
    return m ? parseFloat(m[0]) : null
  }
  return null
}

function deviationColor(pct: number): string {
  if (Math.abs(pct) < 5) return "green"
  if (Math.abs(pct) < 15) return "orange"
  return "red"
}

interface PropEntry {
  readonly key: string
  readonly label: string
  readonly computed: string
  readonly refValue: string | null
  readonly deviation: string | null
  readonly deviationPct: number | null
}

function parseVerifiedProps(props: Record<string, unknown>): readonly PropEntry[] {
  return Object.entries(props).map(([key, raw]) => {
    const label = getLabel(key)

    if (typeof raw === "number" || typeof raw === "string") {
      return {
        key,
        label,
        computed: String(raw),
        refValue: null,
        deviation: null,
        deviationPct: null,
      }
    }

    if (raw && typeof raw === "object") {
      const obj = raw as Record<string, unknown>
      const computed = obj.computed ?? obj.calculated ?? obj.value ?? obj.result
      const ref =
        obj.experimental ?? obj.experimental_data ?? obj.reference ?? obj.reference_value
      const refNum = ref != null ? extractNumeric(ref) : null
      const compNum = extractNumeric(computed)

      let deviation: string | null = null
      let deviationPct: number | null = null
      if (refNum != null && compNum != null && refNum !== 0) {
        const pct = ((compNum - refNum) / Math.abs(refNum)) * 100
        deviation = `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`
        deviationPct = pct
      }

      return {
        key,
        label,
        computed: computed != null ? String(computed) : JSON.stringify(raw),
        refValue: ref != null ? String(ref) : null,
        deviation,
        deviationPct,
      }
    }

    return {
      key,
      label,
      computed: JSON.stringify(raw),
      refValue: null,
      deviation: null,
      deviationPct: null,
    }
  })
}

interface PotentialVerifiedPropsProps {
  readonly detail: PotentialDetail
}

export function PotentialVerifiedProps({ detail }: PotentialVerifiedPropsProps) {
  const { verified_props } = detail

  if (!verified_props || Object.keys(verified_props).length === 0) {
    return <Empty description="暂无验证性质数据" />
  }

  const entries = parseVerifiedProps(verified_props)
  const hasRef = entries.some((e) => e.refValue != null)

  const columns: ColumnsType<PropEntry> = [
    {
      title: "属性名称",
      dataIndex: "label",
      key: "label",
    },
    {
      title: "计算值",
      dataIndex: "computed",
      key: "computed",
      render: (v: string) => <Text code>{v}</Text>,
    },
  ]

  if (hasRef) {
    columns.push(
      {
        title: "参考值",
        dataIndex: "refValue",
        key: "refValue",
        render: (v: string | null) => (v ?? "-"),
      },
      {
        title: "偏差",
        dataIndex: "deviation",
        key: "deviation",
        render: (dev: string | null, record: PropEntry) => {
          if (dev == null || record.deviationPct == null) return "-"
          const color = deviationColor(record.deviationPct)
          return <Tag color={color}>{dev}</Tag>
        },
      },
    )
  }

  return (
    <Table<PropEntry>
      columns={columns}
      dataSource={[...entries]}
      rowKey="key"
      pagination={false}
      size="small"
    />
  )
}
