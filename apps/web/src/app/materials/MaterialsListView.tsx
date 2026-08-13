"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Input, Pagination, Spin, Empty, Table, Tag, Typography, Space } from "antd"
import type { ColumnsType } from "antd/es/table"
import { request } from "@/lib/api-client"

const { Title, Text } = Typography

// ── Types ──────────────────────────────────────────────────────────────

interface MaterialItem {
  readonly id: string
  readonly name: string
  readonly formula: string | null
  readonly crystal_structure: string | null
  readonly description: string | null
  readonly is_active: boolean
  readonly created_at: string
  readonly updated_at: string
}

interface PaginatedData {
  readonly items: ReadonlyArray<MaterialItem>
  readonly total: number
  readonly page: number
  readonly per_page: number
}

interface ApiResponse<T> {
  readonly success: boolean
  readonly data: T
}

interface ViewState {
  readonly materials: ReadonlyArray<MaterialItem>
  readonly total: number
  readonly loading: boolean
  readonly error: string | null
}

const INITIAL_STATE: ViewState = {
  materials: [],
  total: 0,
  loading: true,
  error: null,
}

const PAGE_SIZE = 20

// ── Table columns ──────────────────────────────────────────────────────

function buildColumns(searchQuery: string): ColumnsType<MaterialItem> {
  return [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      render: (name: string, record: MaterialItem) => (
        <Link href={`/materials/${record.id}`}>{name}</Link>
      ),
      filteredValue: searchQuery ? [searchQuery] as [string] : undefined,
      onFilter: (value, record) =>
        record.name.toLowerCase().includes((value as string).toLowerCase()),
    },
    {
      title: "化学式",
      dataIndex: "formula",
      key: "formula",
      render: (formula: string | null) =>
        formula ?? <Text type="secondary">—</Text>,
    },
    {
      title: "晶体结构",
      dataIndex: "crystal_structure",
      key: "crystal_structure",
      render: (cs: string | null) =>
        cs ? <Tag>{cs}</Tag> : <Text type="secondary">—</Text>,
    },
    {
      title: "状态",
      dataIndex: "is_active",
      key: "is_active",
      render: (active: boolean) =>
        active ? (
          <Tag color="green">活跃</Tag>
        ) : (
          <Tag color="default">停用</Tag>
        ),
      width: 80,
    },
    {
      title: "操作",
      key: "actions",
      width: 200,
      render: (_: unknown, record: MaterialItem) => (
        <Space size="small">
          <Link href={`/materials/${record.id}`}>详情</Link>
          <Link href={`/materials/${record.id}/properties`}>物性</Link>
          <Link href={`/materials/${record.id}/graph`}>图谱</Link>
        </Space>
      ),
    },
  ]
}

// ── Component ──────────────────────────────────────────────────────────

export function MaterialsListView() {
  const [state, setState] = useState<ViewState>(INITIAL_STATE)
  const [page, setPage] = useState(1)
  const [searchQuery, setSearchQuery] = useState("")

  const fetchMaterials = useCallback(async (pageNum: number): Promise<void> => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    try {
      let endpoint = `/api/v1/materials?page=${pageNum}&per_page=${PAGE_SIZE}`
      if (searchQuery.trim()) {
        endpoint = `/api/v1/materials/search?q=${encodeURIComponent(searchQuery.trim())}&page=${pageNum}&per_page=${PAGE_SIZE}`
      }
      const resp = await request<ApiResponse<PaginatedData>>(endpoint)
      setState({
        materials: resp.data.items,
        total: resp.data.total,
        loading: false,
        error: null,
      })
    } catch (err) {
      setState({
        materials: [],
        total: 0,
        loading: false,
        error: err instanceof Error ? err.message : "加载失败",
      })
    }
  }, [searchQuery])

  useEffect(() => {
    const timer = setTimeout(() => { void fetchMaterials(page) }, 300)
    return () => clearTimeout(timer)
  }, [page, fetchMaterials])

  const handleSearch = (value: string) => {
    setSearchQuery(value)
    setPage(1)
  }

  return (
    <div className="max-w-[1200px] mx-auto px-6 py-8">
      <Title level={2}>材料列表</Title>
      <Text type="secondary">
        浏览数据库中全部核燃料与结构材料，共 {state.total} 条记录
      </Text>

      <div className="mt-4 mb-4">
        <Input.Search
          placeholder="搜索材料名称、化学式或别名"
          allowClear
          size="large"
          onSearch={handleSearch}
          style={{ maxWidth: 400 }}
        />
      </div>

      {state.error && (
        <div className="mb-4 p-3 bg-red-900/30 border border-red-700 rounded text-red-300">
          {state.error}
        </div>
      )}

      <Spin spinning={state.loading}>
        {state.materials.length === 0 && !state.loading ? (
          <Empty description="暂无材料数据" />
        ) : (
          <>
            <Table<MaterialItem>
              columns={buildColumns(searchQuery)}
              dataSource={[...state.materials]}
              rowKey="id"
              pagination={false}
              size="middle"
              scroll={{ x: 700 }}
            />
            <div className="mt-4 flex justify-center">
              <Pagination
                current={page}
                total={state.total}
                pageSize={PAGE_SIZE}
                onChange={(p) => setPage(p)}
                showSizeChanger={false}
                showTotal={(total) => `共 ${total} 条`}
              />
            </div>
          </>
        )}
      </Spin>
    </div>
  )
}
