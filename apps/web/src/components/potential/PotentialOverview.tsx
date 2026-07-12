"use client"

import { Descriptions, Tag, Space, Typography } from "antd"
import type { PotentialDetail } from "@/lib/potentials-api"

const { Paragraph } = Typography

const TYPE_COLOR: Record<string, string> = {
  EAM: "blue",
  MEAM: "green",
  MTP: "purple",
  ACE: "orange",
}

const STATUS_LABELS: Record<string, string> = {
  unverified: "未验证",
  pending: "验证中",
  verified: "已验证",
  failed: "验证失败",
}

const STATUS_COLORS: Record<string, string> = {
  unverified: "default",
  pending: "processing",
  verified: "success",
  failed: "error",
}

interface PotentialOverviewProps {
  readonly detail: PotentialDetail
}

function typeColor(type: string): string {
  return TYPE_COLOR[type] ?? "default"
}

function asStringArray(value: unknown): readonly string[] {
  if (!Array.isArray(value)) return []
  return value.filter((v): v is string => typeof v === "string")
}

function asString(value: unknown): string {
  if (value == null) return ""
  if (typeof value === "string") return value
  return String(value)
}

export function PotentialOverview({ detail }: PotentialOverviewProps) {
  const {
    name,
    type,
    format,
    elements,
    system_name,
    description,
    source,
    source_doi,
    version,
    tags,
    references,
    verification_status,
  } = detail

  const doi = source_doi ?? asString(references?.[0]?.doi)

  return (
    <Descriptions
      bordered
      column={2}
      size="small"
      labelStyle={{ width: 120, fontWeight: 600 }}
    >
      <Descriptions.Item label="名称">{name}</Descriptions.Item>
      <Descriptions.Item label="类型">
        <Tag color={typeColor(type)}>{type}</Tag>
      </Descriptions.Item>
      <Descriptions.Item label="验证状态" span={2}>
        <Tag color={STATUS_COLORS[verification_status] ?? "default"}>
          {STATUS_LABELS[verification_status] ?? verification_status}
        </Tag>
      </Descriptions.Item>
      <Descriptions.Item label="格式">{format || "-"}</Descriptions.Item>
      <Descriptions.Item label="版本">{version || "-"}</Descriptions.Item>
      <Descriptions.Item label="元素" span={2}>
        <Space wrap size={[0, 4]}>
          {elements.length > 0 ? (
            elements.map((el) => <Tag key={el}>{el}</Tag>)
          ) : (
            <span>-</span>
          )}
        </Space>
      </Descriptions.Item>
      <Descriptions.Item label="体系">{system_name || "-"}</Descriptions.Item>
      <Descriptions.Item label="来源">{source || "-"}</Descriptions.Item>
      <Descriptions.Item label="DOI" span={2}>
        {doi ? (
          <a
            href={`https://doi.org/${doi}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            {doi}
          </a>
        ) : (
          "-"
        )}
      </Descriptions.Item>
      <Descriptions.Item label="描述" span={2}>
        <Paragraph style={{ marginBottom: 0 }}>
          {description || "暂无描述"}
        </Paragraph>
      </Descriptions.Item>
      <Descriptions.Item label="标签" span={2}>
        <Space wrap size={[0, 4]}>
          {asStringArray(tags).length > 0 ? (
            asStringArray(tags).map((t) => (
              <Tag key={t} color="default">
                {t}
              </Tag>
            ))
          ) : (
            <span>-</span>
          )}
        </Space>
      </Descriptions.Item>
    </Descriptions>
  )
}
