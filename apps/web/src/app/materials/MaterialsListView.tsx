"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import {
  Empty,
  Input,
  Pagination,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd"
import type { ColumnsType } from "antd/es/table"
import { request } from "@/lib/api-client"
import {
  listMaterialCategories,
  type MaterialCategory,
} from "@/lib/materials-api"

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

// ── URL ↔ state helpers (NFM-3917 / Tier 1D) ──────────────────────────
//
// The category filter selection is the only piece of view state that
// needs to be shareable / back-button-correct, so we put it in the URL
// via `?category_id=<uuid>` and round-trip it through `useSearchParams`.
// `searchQuery` stays local — it is debounced into the API call and
// clearing it is a one-action thing users rarely need to share.

function parseCategoryParam(raw: string | null): string | null {
  if (!raw) return null
  const trimmed = raw.trim()
  if (!trimmed) return null
  // Defend against junk in the URL: a UUID-shaped string only.
  // Otherwise leave the dropdown cleared (better than silently
  // filtering everything to zero rows).
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    trimmed,
  )
    ? trimmed
    : null
}

function parsePageParam(raw: string | null): number {
  if (!raw) return 1
  const n = Number.parseInt(raw, 10)
  return Number.isFinite(n) && n >= 1 ? n : 1
}

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
      filteredValue: searchQuery ? ([searchQuery] as [string]) : undefined,
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
  const router = useRouter()
  const searchParams = useSearchParams()

  // URL state — category_id is shareable; page is the in-URL pagination.
  const categoryId = useMemo(
    () => parseCategoryParam(searchParams.get("category_id")),
    [searchParams],
  )
  const page = useMemo(
    () => parsePageParam(searchParams.get("page")),
    [searchParams],
  )

  // Local UI state
  const [state, setState] = useState<ViewState>(INITIAL_STATE)
  const [searchQuery, setSearchQuery] = useState("")
  const [categories, setCategories] = useState<ReadonlyArray<MaterialCategory>>(
    [],
  )

  // Load the taxonomy once. The endpoint is public and the page is
  // usually long-lived; a single fetch on mount is correct here.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      const cats = await listMaterialCategories()
      if (!cancelled) setCategories(cats)
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const fetchMaterials = useCallback(async (): Promise<void> => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    try {
      const params = new URLSearchParams()
      params.set("page", String(page))
      params.set("per_page", String(PAGE_SIZE))
      if (categoryId) params.set("category_id", categoryId)
      const trimmedQuery = searchQuery.trim()
      let endpoint: string
      if (trimmedQuery) {
        // /materials/search composes with category_id — see NFM-3917 CPO
        // decision. Search and category filter are NOT mutually exclusive.
        endpoint = `/api/v1/materials/search?q=${encodeURIComponent(trimmedQuery)}`
      } else {
        endpoint = `/api/v1/materials?sort=name&order=asc`
      }
      endpoint = `${endpoint}${endpoint.includes("?") ? "&" : "?"}${params.toString()}`
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
  }, [categoryId, page, searchQuery])

  useEffect(() => {
    const timer = setTimeout(() => {
      void fetchMaterials()
    }, 300)
    return () => clearTimeout(timer)
  }, [fetchMaterials])

  // ── URL mutation helpers (NFM-3917 / Tier 1D) ────────────────────────

  const updateUrl = useCallback(
    (next: { categoryId?: string | null; page?: number }) => {
      const sp = new URLSearchParams(searchParams.toString())
      if ("categoryId" in next) {
        if (next.categoryId) sp.set("category_id", next.categoryId)
        else sp.delete("category_id")
      }
      if ("page" in next) {
        if (next.page && next.page > 1) sp.set("page", String(next.page))
        else sp.delete("page")
      }
      const qs = sp.toString()
      router.replace(qs ? `/materials?${qs}` : "/materials", { scroll: false })
    },
    [router, searchParams],
  )

  const handleSearch = (value: string) => {
    setSearchQuery(value)
    updateUrl({ page: 1 })
  }

  const handleCategoryChange = (next: string | undefined) => {
    // Changing category resets to page 1 (CPO decision) so users do not
    // land on a page that no longer exists for the narrowed result set.
    updateUrl({ categoryId: next ?? null, page: 1 })
  }

  const handlePageChange = (next: number) => {
    updateUrl({ page: next })
  }

  return (
    <div className="max-w-[1200px] mx-auto px-6 py-8">
      <Title level={2}>材料列表</Title>
      <Text type="secondary">
        浏览数据库中全部核燃料与结构材料，共 {state.total} 条记录
      </Text>

      <div
        className="mt-4 mb-4 flex flex-wrap items-center gap-3"
        data-testid="materials-filter-bar"
      >
        <Input.Search
          placeholder="搜索材料名称、化学式或别名"
          allowClear
          size="large"
          onSearch={handleSearch}
          style={{ maxWidth: 400 }}
        />
        <Select
          aria-label="category-filter"
          data-testid="materials-category-select"
          placeholder="全部类别"
          allowClear
          size="large"
          value={categoryId ?? undefined}
          onChange={handleCategoryChange}
          style={{ minWidth: 220 }}
          loading={categories.length === 0}
          options={categories.map((c) => ({
            value: c.id,
            label: c.name,
          }))}
          notFoundContent={
            <Text type="secondary" className="px-2">
              暂无类别数据
            </Text>
          }
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
                onChange={handlePageChange}
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