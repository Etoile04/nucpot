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
      className="h-full !bg-gray-800 !border-gray-700"
      styles={{ body: { display: "flex", flexDirection: "column", gap: 8 } }}
    >
      <Space wrap size={[0, 4]}>
        <Tag color={tagColor}>{type}</Tag>
      </Space>

      <Link href={`/potential/${id}`} className="no-underline">
        <Typography.Title level={5} className="!m-0 text-white">
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
          className="!mb-0"
        >
          {description}
        </Paragraph>
      )}

      <div className="mt-auto pt-1">
        <Link href={`/potential/${id}`} className="text-blue-400 hover:text-blue-300">查看详情</Link>
      </div>
    </Card>
  )
}
