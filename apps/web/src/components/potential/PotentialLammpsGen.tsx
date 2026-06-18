"use client"

import { Typography, Alert } from "antd"
import type { PotentialDetail } from "@/lib/potentials-api"

const { Paragraph, Text } = Typography

interface PotentialLammpsGenProps {
  readonly detail: PotentialDetail
}

function asNumberArray(value: unknown): readonly number[] {
  if (!Array.isArray(value)) return []
  return value.filter((v): v is number => typeof v === "number")
}

function buildScript(detail: PotentialDetail): string {
  const displayName = detail.display_name ?? detail.name
  const pairStyle = asString(detail.lammps_config?.pair_style)
  const pairCoeff = asString(detail.lammps_config?.pair_coeff ?? "")

  const applicability = detail.applicability as
    | { temperatureRange?: number[] }
    | undefined
  const temperatureRange = asNumberArray(applicability?.temperatureRange)
  const exampleTemp = temperatureRange.length === 2
    ? Math.round((temperatureRange[0]! + temperatureRange[1]!) / 2)
    : 300

  return `# LAMMPS 输入脚本 — ${displayName}
# 自动生成 by NFMD，请根据实际模拟需求修改

units           metal
dimension       3
boundary        p p p
atom_style      atomic

# 读取原子模型文件（需自行准备）
read_data       model.data

# 势函数设置
pair_style      ${pairStyle}
pair_coeff      ${pairCoeff}

# 邻居列表
neighbor        2.0 bin
neigh_modify    every 1 delay 0 check yes

# 时间步长（建议值，请根据体系调整）
timestep        0.001

# 能量最小化示例
minimize        1e-10 1e-10 1000 10000

# 或 MD 模拟示例
# velocity      all create ${exampleTemp} 87287 dist gaussian
# fix           1 all npt temp ${exampleTemp} ${exampleTemp} 0.1 iso 0 0 1
# thermo        100
# thermo_style  custom step temp pe ke etotal press vol
# run           10000
`
}

function asString(value: unknown): string {
  if (value == null) return ""
  if (typeof value === "string") return value
  return String(value)
}

export function PotentialLammpsGen({ detail }: PotentialLammpsGenProps) {
  const pairStyle = asString(detail.lammps_config?.pair_style)
  const note = asString(detail.lammps_config?.note)

  if (!pairStyle) {
    return (
      <Typography>
        <Text type="secondary">暂无 LAMMPS 配置信息</Text>
      </Typography>
    )
  }

  const script = buildScript(detail)
  const pairCoeff = asString(detail.lammps_config?.pair_coeff ?? "")

  return (
    <Typography>
      <Paragraph>
        <Text strong>LAMMPS 命令：</Text>
      </Paragraph>
      <Paragraph>
        <Text code style={{ display: "block", whiteSpace: "pre-wrap" }}>
          {`pair_style ${pairStyle}`}
          {pairCoeff ? `\npair_coeff ${pairCoeff}` : ""}
        </Text>
      </Paragraph>

      <Paragraph>
        <Text strong>完整 LAMMPS 输入脚本模板：</Text>
      </Paragraph>
      <Paragraph
        copyable={{ text: script }}
        style={{
          background: "#0d1117",
          color: "#c9d1d9",
          padding: 16,
          borderRadius: 8,
          fontFamily:
            "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
          fontSize: 13,
          whiteSpace: "pre-wrap",
          margin: 0,
        }}
      >
        {script}
      </Paragraph>

      {note && (
        <Alert
          style={{ marginTop: 12 }}
          type="warning"
          showIcon
          message={`⚠️ ${note}`}
        />
      )}
      <Alert
        style={{ marginTop: 8 }}
        type="info"
        showIcon
        message="此脚本为模板，请根据实际模拟需求修改参数"
      />
    </Typography>
  )
}
