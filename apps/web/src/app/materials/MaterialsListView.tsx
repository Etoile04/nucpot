"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
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
  getUncategorizedMaterialCount,
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
  const searchParams = useSearchParams()

  // URL state — category_id is shareable; page is the in-URL pagination.
  //
  // State is the source of truth for the data fetch; the URL is kept in
  // sync as a side-effect (see the URL-sync effect below). Reading the
  // URL only happens once via the lazy initial state — we do NOT derive
  // `categoryId`/`page` from `useSearchParams()` on every render, because
  // Next.js 16's App Router `router.replace` does not always propagate
  // back to `useSearchParams` when the initial render already had
  // non-empty search params (NFM-3917 AC-4 bug: clicking a category on a
  // deep-linked `/materials?page=2` did not filter the table or update
  // the URL). Driving the data fetch from local React state avoids that
  // round-trip entirely.
  const [categoryId, setCategoryId] = useState<string | null>(() =>
    parseCategoryParam(searchParams.get("category_id")),
  )
  const [page, setPage] = useState<number>(() =>
    parsePageParam(searchParams.get("page")),
  )

  // Local UI state
  const [state, setState] = useState<ViewState>(INITIAL_STATE)
  const [searchQuery, setSearchQuery] = useState("")
  const [categories, setCategories] = useState<ReadonlyArray<MaterialCategory>>(
    [],
  )
  // NFM-4030: number of materials with `category_id IS NULL`. Null while
  // loading or on network error so the badge stays hidden rather than
  // flashing a misleading "0".
  const [uncategorizedCount, setUncategorizedCount] = useState<number | null>(
    null,
  )

  // Load the taxonomy + uncategorized count once. The endpoints are
  // public and the page is usually long-lived; a single fetch on mount
  // is correct here. The two requests are independent so we fire them
  // in parallel and update state independently — no need to await both
  // before the dropdown renders.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      const [cats, uncat] = await Promise.all([
        listMaterialCategories(),
        getUncategorizedMaterialCount(),
      ])
      if (cancelled) return
      setCategories(cats)
      setUncategorizedCount(uncat)
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // Sync state → URL (NFM-3917 / Tier 1D, AC-4 fix).
  //
  // Build the canonical URL from current state and push it via
  // `window.history.replaceState`. We deliberately bypass
  // `router.replace` here because (a) it does not always propagate back
  // to `useSearchParams` in Next.js 16, leaving downstream `useMemo`s
  // stale, and (b) it triggers a server round-trip for App Router pages
  // we know only need a client-side filter update. The browser back
  // button will still walk the canonical URL stack since we only ever
  // `replaceState` (no `pushState`).
  //
  // A ref tracks the last URL we wrote so we don't replaceState on every
  // re-render — only when state actually changed.
  const lastUrlRef = useRef<string | null>(null)
  useEffect(() => {
    const sp = new URLSearchParams()
    if (categoryId) sp.set("category_id", categoryId)
    if (page > 1) sp.set("page", String(page))
    const qs = sp.toString()
    const target = qs ? `/materials?${qs}` : "/materials"
    if (typeof window === "undefined") return
    if (lastUrlRef.current === target) return
    lastUrlRef.current = target
    const currentSearch =
      window.location.pathname + (window.location.search || "")
    if (currentSearch === target) return
    window.history.replaceState(null, "", target)
  }, [categoryId, page])

  const fetchMaterials = async (): Promise<void> => {
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
  }

  useEffect(() => {
    const timer = setTimeout(() => {
      void fetchMaterials()
    }, 300)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryId, page, searchQuery])

  // ── Handlers (NFM-3917 / Tier 1D) ─────────────────────────────────────

  const handleSearch = (value: string) => {
    setSearchQuery(value)
    setPage(1)
  }

  const handleCategoryChange = (next: string | undefined) => {
    // Changing category resets to page 1 (CPO decision) so users do not
    // land on a page that no longer exists for the narrowed result set.
    setCategoryId(next ?? null)
    setPage(1)
  }

  const handlePageChange = (next: number) => {
    setPage(next)
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
        {/* NFM-4030 silent-data-gap badge.
            Renders only when we know there are uncategorized materials
            AND no category filter is active (the badge is redundant once
            the user is already looking at a specific subset). */}
        {uncategorizedCount !== null && uncategorizedCount > 0 && categoryId === null && (
          <Tag
            color="warning"
            data-testid="materials-uncategorized-badge"
            title="这些材料未分配任何类别,不会出现在上方任何类别筛选结果中"
          >
            {uncategorizedCount} 条材料尚未分类
          </Tag>
        )}
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