"use client"

// NFM-5321 hotfix (deploy of d4c33eb6a): the detail page is a Server
// Component, and antd `Typography`'s static subcomponents (`.Title` /
// `.Text`) resolve to `undefined` in the Turbopack server-component
// standalone runtime — every /datasets/{id} request SSR-crashed with
// `Element type is invalid: … got: undefined` (HTTP 500, empty
// `__next_error__` document; compile/vitest/tsc all green, so CI could
// not see it). Named imports (`Alert`, `Descriptions`, `Tag`) render
// fine server-side; only the Typography statics vanish. The fix moves
// the antd-Typography-using subtree behind a "use client" boundary —
// the exact pattern of the sibling materials detail page
// (materials/[id]/MaterialDetailContent.tsx) — keeping the rendered
// antd DOM byte-identical to what NFM-5327 reviewed.

import Link from "next/link"
import { Alert, Descriptions, Tag, Typography } from "antd"
import { formatDate } from "@/lib/format-date"
import type { DatasetDetail } from "@/lib/datasets-server"

const { Title, Text } = Typography

/**
 * Client-rendered dataset body: header (title / id chip / verification +
 * attribution badges), placeholder disclosure banner, and the field
 * Descriptions table. Receives the server-fetched dataset as a plain
 * serializable prop.
 */
export function DatasetDetailContent({ dataset }: { dataset: DatasetDetail }) {
  return (
    <>
      <header className="space-y-2 mb-6">
        <Title level={2} className="!m-0">
          {dataset.title}
        </Title>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <Text type="secondary" className="font-mono text-xs" title={dataset.id}>
            {dataset.id.slice(0, 8)}
          </Text>
          {dataset.is_verified ? (
            <Tag color="green">已审核</Tag>
          ) : (
            <Tag color="default">未审核</Tag>
          )}
          <AttributionBadge status={dataset.attribution.status} />
        </div>
      </header>

      {/* NFM-4159 §5.2 attribution disclosure: when status is
          'placeholder' the dataset title already carries the
          disclosure per CEO §4.2 — this block is a confirmation
          banner, not the primary disclosure surface. NFM-5321 D1:
          copy is user-facing; internal ticket refs stay in code
          comments only. */}
      {dataset.attribution.status === "placeholder" ? (
        <Alert
          type="warning"
          showIcon
          className="mb-6"
          message="占位数据集"
          description={
            <span>
              此数据集由历史数据复原生成，原始文献归属不完整；标题中的占位标注即为此状态的说明，数据字段仍可正常浏览。
            </span>
          }
        />
      ) : null}

      <Descriptions
        column={1}
        bordered
        size="middle"
        items={[
          {
            key: "material",
            label: "材料",
            // NFM-5321 D2: resolved name via expand=material; the
            // raw UUID never renders. 「—」 when unresolved.
            children: dataset.material_name ? (
              <Link
                href={`/materials/${dataset.material_id}`}
                className="text-blue-400 hover:text-blue-300 hover:underline"
              >
                {dataset.material_name}
              </Link>
            ) : (
              <Text type="secondary">—</Text>
            ),
          },
          {
            key: "source",
            label: "数据源",
            // NFM-5321 D2: resolved title via expand=source. 无 = no
            // linked source; 「—」 = linked but unresolved title.
            children:
              dataset.source_id === null ? (
                <Text type="secondary">无</Text>
              ) : dataset.source_title ? (
                <Link
                  href={`/publications/${dataset.source_id}`}
                  className="text-blue-400 hover:text-blue-300 hover:underline"
                >
                  {dataset.source_title}
                </Link>
              ) : (
                <Text type="secondary">—</Text>
              ),
          },
          {
            key: "measurement_date",
            label: "测量日期",
            children: formatDate(dataset.measurement_date),
          },
          {
            key: "description",
            label: "描述",
            children: dataset.description ? (
              <span className="whitespace-pre-wrap text-sm">
                {dataset.description}
              </span>
            ) : (
              <Text type="secondary">无</Text>
            ),
          },
          {
            key: "created",
            // NFM-5321 D2: date-only rendering (YYYY-MM-DD), matching
            // the list view — no raw ISO 8601 wire format.
            label: "创建时间",
            children: formatDate(dataset.created_at),
          },
          {
            key: "updated",
            label: "更新时间",
            children: formatDate(dataset.updated_at),
          },
        ]}
      />
    </>
  )
}

/** 中文标签 for the §5.2 attribution status enum (NFM-5321 D2). */
function AttributionBadge({
  status,
}: {
  status: "placeholder" | "intact"
}) {
  if (status === "placeholder") {
    return (
      <Tag color="orange" title="历史复原数据，归属信息不完整">
        归属占位
      </Tag>
    )
  }
  return (
    <Tag color="blue" title="原始文献归属信息完整">
      归属完整
    </Tag>
  )
}
