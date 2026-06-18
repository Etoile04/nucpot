"use client"

import Link from "next/link"
import { Card, Tag, Space, Typography } from "antd"
import type { PotentialSummary } from "@/lib/potentials-api"

const { Paragraph } = Typography

const TYPE_COLOR: Record<string, string> = {
  EAM: "blue",
  MEAM: "green",
  MTP: "purple",
  ACE: "orange",
}

interface PotentialCardProps {
  readonly potential: PotentialSummary
}

export function PotentialCard({ potential }: PotentialCardProps) {
  const { id, name, type, elements, description } = potential
  const tagColor = TYPE_COLOR[type] ?? "default"

  return (
    <Card
      style={{ height: "100%" }}
      styles={{ body: { display: "flex", flexDirection: "column", gap: 8 } }}
    >
      <Space wrap size={[0, 4]}>
        <Tag color={tagColor}>{type}</Tag>
      </Space>

      <Link href={`/potential/${id}`} style={{ textDecoration: "none" }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          {name}
        </Typography.Title>
      </Link>

      {elements.length > 0 && (
        <Space wrap size={[0, 4]}>
          {elements.map((el) => (
            <Tag key={el}>{el}</Tag>
          ))}
        </Space>
      )}

      {description && (
        <Paragraph
          type="secondary"
          ellipsis={{ rows: 2 }}
          style={{ marginBottom: 0 }}
        >
          {description}
        </Paragraph>
      )}

      <div style={{ marginTop: "auto", paddingTop: 4 }}>
        <Link href={`/potential/${id}`}>查看详情</Link>
      </div>
    </Card>
  )
}
