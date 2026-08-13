"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Typography, Spin, Alert, Descriptions, Table, Space, Button } from "antd"
import type { ColumnsType } from "antd/es/table"
import { request } from "@/lib/api-client"

const { Title, Text } = Typography

// ── Types ──────────────────────────────────────────────────────────────

interface MaterialAlias {
  readonly alias_name: string
  readonly alias_type: string
  readonly source: string
}

interface MaterialComposition {
  readonly element: string
  readonly fraction: number
}

interface MaterialDetail {
  readonly id: string
  readonly name: string
  readonly formula: string | null
  readonly crystal_structure: string | null
  readonly description: string | null
  readonly is_active: boolean
  readonly created_at: string
  readonly updated_at: string
  readonly aliases: ReadonlyArray<MaterialAlias>
  readonly composition: ReadonlyArray<MaterialComposition>
}

interface ApiResponse<T> {
  readonly success: boolean
  readonly data: T
}

interface MaterialDetailContentProps {
  readonly materialId: string
}

interface ViewState {
  readonly material: MaterialDetail | null
  readonly loading: boolean
  readonly error: string | null
}

const INITIAL_STATE: ViewState = {
  material: null,
  loading: true,
  error: null,
}

// ── Table columns ──────────────────────────────────────────────────────

const aliasColumns: ColumnsType<MaterialAlias> = [
  {
    title: "别名",
    dataIndex: "alias_name",
    key: "alias_name",
  },
  {
    title: "类型",
    dataIndex: "alias_type",
    key: "alias_type",
  },
  {
    title: "来源",
    dataIndex: "source",
    key: "source",
  },
]

const compositionColumns: ColumnsType<MaterialComposition> = [
  {
    title: "元素",
    dataIndex: "element",
    key: "element",
  },
  {
    title: "含量 (%)",
    dataIndex: "fraction",
    key: "fraction",
    render: (value: number) => {
      if (typeof value !== "number") return "-"
      return `${(value * 100).toFixed(2)}%`
    },
  },
]

// ── Component ──────────────────────────────────────────────────────────

export function MaterialDetailContent({
  materialId,
}: MaterialDetailContentProps) {
  const [state, setState] = useState<ViewState>(INITIAL_STATE)

  const fetchData = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }))

    try {
      const response = await request<ApiResponse<MaterialDetail>>(
        `/api/v1/materials/${materialId}`,
      )
      setState({
        material: response.data,
        loading: false,
        error: null,
      })
    } catch (err) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: err instanceof Error ? err.message : "加载失败",
      }))
    }
  }, [materialId])

  useEffect(() => {
    void fetchData()
  }, [fetchData])

  // Loading state
  if (state.loading) {
    return (
      <main className="max-w-[1200px] mx-auto px-6 py-8">
        <div className="flex items-center justify-center py-20">
          <Spin tip="加载中...">
            <div />
          </Spin>
        </div>
      </main>
    )
  }

  // Error state
  if (state.error) {
    return (
      <main className="max-w-[1200px] mx-auto px-6 py-8">
        <Alert
          type="error"
          message="加载失败"
          description={state.error}
          showIcon
          action={
            <Button size="small" onClick={() => void fetchData()}>
              重试
            </Button>
          }
        />
      </main>
    )
  }

  const m = state.material

  return (
    <main className="max-w-[1200px] mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <Title level={2} className="!m-0 text-white">
            {m?.name ?? "材料详情"}
          </Title>
          <Text type="secondary">
            {m?.formula ? `化学式：${m.formula}` : `材料 ID：${materialId}`}
          </Text>
        </div>
        <Link
          href="/browse"
          className="text-blue-400 hover:text-blue-300 text-sm"
        >
          返回浏览
        </Link>
      </div>

      {m && (
        <>
          {/* Basic info */}
          <Descriptions
            bordered
            column={2}
            className="mb-6"
            items={[
              {
                key: "name",
                label: "名称",
                children: m.name,
              },
              {
                key: "formula",
                label: "化学式",
                children: m.formula ?? "-",
              },
              {
                key: "crystal_structure",
                label: "晶体结构",
                children: m.crystal_structure ?? "-",
              },
              {
                key: "is_active",
                label: "状态",
                children: m.is_active ? "活跃" : "停用",
              },
              {
                key: "created_at",
                label: "创建时间",
                children: m.created_at,
              },
              {
                key: "updated_at",
                label: "更新时间",
                children: m.updated_at,
              },
              {
                key: "description",
                label: "描述",
                span: 2,
                children: m.description ?? "-",
              },
            ]}
          />

          {/* Navigation buttons */}
          <Space className="mb-6">
            <Link href={`/materials/${materialId}/graph`}>
              <Button type="primary">查看知识图谱</Button>
            </Link>
            <Link href={`/materials/${materialId}/properties`}>
              <Button type="primary">查看属性</Button>
            </Link>
          </Space>

          {/* Aliases table */}
          {m.aliases.length > 0 && (
            <div className="mb-6">
              <Title level={4} className="!mb-3 text-white">
                别名
              </Title>
              <Table<MaterialAlias>
                columns={aliasColumns}
                dataSource={m.aliases}
                rowKey={(record, index) =>
                  `${record.alias_name}-${record.alias_type}-${index}`
                }
                pagination={false}
                size="small"
              />
            </div>
          )}

          {/* Composition table */}
          {m.composition.length > 0 && (
            <div className="mb-6">
              <Title level={4} className="!mb-3 text-white">
                组成
              </Title>
              <Table<MaterialComposition>
                columns={compositionColumns}
                dataSource={m.composition}
                rowKey={(record, index) => `${record.element}-${index}`}
                pagination={false}
                size="small"
              />
            </div>
          )}
        </>
      )}
    </main>
  )
}
