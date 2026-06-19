"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Pagination, Spin, Empty, Space, Typography } from "antd"
import type { PaginationProps } from "antd"
import { PotentialCard } from "@/components/potential/PotentialCard"
import { ElementFilter } from "@/components/potential/ElementFilter"
import {
  listPotentials,
  type ListParams,
  type PotentialSummary,
  type PotentialListResult,
} from "@/lib/potentials-api"

const { Title, Text } = Typography

const TYPES: readonly string[] = ['EAM', 'MEAM', 'ML', 'MTP', 'ACE', 'Buckingham', 'other']

type SortField = "updated" | "name" | "type"

const SORT_OPTIONS: readonly { readonly label: string; readonly value: SortField }[] = [
  { label: "最近更新", value: "updated" },
  { label: "按名称", value: "name" },
  { label: "按类型", value: "type" },
]

const PAGE_SIZE = 12

interface StatsData {
  readonly elements: readonly string[]
}

interface BrowseState {
  readonly potentials: readonly PotentialSummary[]
  readonly total: number
  readonly page: number
  readonly loading: boolean
  readonly error: string | null
}

const INITIAL_STATE: BrowseState = {
  potentials: [],
  total: 0,
  page: 1,
  loading: true,
  error: null,
}

export function BrowseView() {
  const [allElements, setAllElements] = useState<string[]>([])
  const [selectedElements, setSelectedElements] = useState<string[]>([])
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set())
  const [sort, setSort] = useState<SortField>("updated")
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false)
  const [state, setState] = useState<BrowseState>(INITIAL_STATE)

  useEffect(() => {
    fetch('/api/stats')
      .then(res => res.json() as Promise<StatsData>)
      .then(data => setAllElements([...data.elements]))
      .catch(() => { /* ignore stats load error */ })
  }, [])

  const loadPage = useCallback(async (elements: string[], types: Set<string>, sortBy: SortField, page: number) => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    const params: ListParams = {
      page,
      limit: PAGE_SIZE,
      sort: sortBy,
    }
    if (elements.length > 0) params.elements = [...elements]
    if (types.size > 0 && types.size < TYPES.length) {
      // Filter to only non-empty selected types
      const active = TYPES.filter(t => types.has(t))
      if (active.length === 1) params.type = active[0]
      else if (active.length > 1) params.type = active.join(',')
    }
    try {
      const result: PotentialListResult = await listPotentials(params)
      setState({
        potentials: result.potentials,
        total: result.total,
        page: result.page,
        loading: false,
        error: null,
      })
    } catch (err) {
      setState({
        potentials: [],
        total: 0,
        page,
        loading: false,
        error: err instanceof Error ? err.message : "加载失败",
      })
    }
  }, [])

  useEffect(() => {
    void loadPage(selectedElements, selectedTypes, sort, 1)
  }, [selectedElements, selectedTypes, sort, loadPage])

  const onPageChange: PaginationProps["onChange"] = (page) => {
    void loadPage(selectedElements, selectedTypes, sort, page)
  }

  const toggleElement = useCallback((el: string) => {
    setSelectedElements(prev =>
      prev.includes(el) ? prev.filter(e => e !== el) : [...prev, el]
    )
  }, [])

  const toggleType = useCallback((t: string) => {
    setSelectedTypes(prev => {
      const next = new Set(prev)
      if (next.has(t)) next.delete(t)
      else next.add(t)
      return next
    })
  }, [])

  const resetFilters = useCallback(() => {
    setSelectedElements([])
    setSelectedTypes(new Set())
  }, [])

  const sidebar = (
    <aside className="space-y-4">
      <div>
        <Text type="secondary" className="text-xs uppercase tracking-wider">函数形式</Text>
        <div className="mt-1.5 space-y-1">
          {TYPES.map(t => (
            <label key={t} className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
              <input
                type="checkbox"
                checked={selectedTypes.has(t)}
                onChange={() => toggleType(t)}
                className="rounded border-gray-600 bg-gray-700 text-blue-500 focus:ring-blue-500"
              />
              {t}
            </label>
          ))}
        </div>
      </div>

      <div>
        <Text type="secondary" className="text-xs uppercase tracking-wider">元素筛选</Text>
        <div className="mt-1.5">
          <ElementFilter
            allElements={allElements}
            selected={selectedElements}
            onToggle={toggleElement}
          />
        </div>
      </div>

      <div>
        <Text type="secondary" className="text-xs uppercase tracking-wider">排序</Text>
        <select
          value={sort}
          onChange={e => setSort(e.target.value as SortField)}
          className="mt-1.5 w-full px-2 py-1.5 rounded bg-gray-700 border border-gray-600 text-sm text-gray-300 focus:outline-none focus:border-blue-500"
        >
          {SORT_OPTIONS.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>

      <button
        onClick={resetFilters}
        className="w-full px-3 py-1.5 rounded bg-gray-700 border border-gray-600 text-sm text-gray-300 hover:border-blue-500/50 transition"
      >
        重置筛选
      </button>
    </aside>
  )

  return (
    <main className="max-w-[1200px] mx-auto px-6 py-8">
      <Space direction="vertical" size="middle" className="w-full mb-6">
        <div className="flex items-center justify-between">
          <Title level={2} className="!m-0 text-white">
            浏览势函数
          </Title>
          <Link href="/search" className="text-blue-400 hover:text-blue-300 text-sm">
            高级检索
          </Link>
        </div>
      </Space>

      {/* Mobile filter toggle */}
      <div className="md:hidden mb-4">
        <button
          onClick={() => setMobileFilterOpen(v => !v)}
          className="px-3 py-1.5 rounded bg-gray-700 border border-gray-600 text-sm text-gray-300"
        >
          {mobileFilterOpen ? '收起筛选' : '展开筛选'}
        </button>
        {mobileFilterOpen && (
          <div className="mt-2 p-3 rounded-lg bg-gray-800 border border-gray-700">
            {sidebar}
          </div>
        )}
      </div>

      <div className="flex gap-6">
        {/* Desktop sidebar */}
        <div className="hidden md:block w-64 shrink-0">
          <div className="sticky top-24 p-4 rounded-lg bg-gray-800 border border-gray-700">
            {sidebar}
          </div>
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          <Spin spinning={state.loading} tip="加载中...">
            {state.error ? (
              <Empty description={`加载失败：${state.error}`} />
            ) : state.potentials.length === 0 && !state.loading ? (
              <Empty description="暂无势函数数据" />
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {state.potentials.map((p) => (
                  <PotentialCard key={p.id} potential={p} />
                ))}
              </div>
            )}
          </Spin>

          {!state.error && state.total > 0 && (
            <Pagination
              current={state.page}
              total={state.total}
              pageSize={PAGE_SIZE}
              onChange={onPageChange}
              showSizeChanger={false}
              showTotal={(total) => `共 ${total} 条`}
              className="mt-8 text-center"
            />
          )}
        </div>
      </div>
    </main>
  )
}
