"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Spin, Empty, Typography } from "antd"
import { PotentialCard } from "@/components/potential/PotentialCard"
import { ElementFilter } from "@/components/potential/ElementFilter"
import { useDebounce } from "@/components/potential/useDebounce"
import {
  listPotentials,
  type ListParams,
  type PotentialListResult,
  type PotentialSummary,
} from "@/lib/potentials-api"

const { Text } = Typography

const DEBOUNCE_MS = 300
const SEARCH_LIMIT = 24

const TYPE_OPTIONS = [
  { label: "全部类型", value: "" },
  { label: "EAM", value: "EAM" },
  { label: "MEAM", value: "MEAM" },
  { label: "ML", value: "ML" },
  { label: "MTP", value: "MTP" },
  { label: "ACE", value: "ACE" },
  { label: "Buckingham", value: "Buckingham" },
  { label: "other", value: "other" },
]

interface StatsData {
  readonly elements: readonly string[]
}

interface SearchState {
  readonly potentials: readonly PotentialSummary[]
  readonly total: number
  readonly loading: boolean
  readonly error: string | null
}

const INITIAL_STATE: SearchState = {
  potentials: [],
  total: 0,
  loading: true,
  error: null,
}

export function SearchView() {
  const [allElements, setAllElements] = useState<string[]>([])
  const [keyword, setKeyword] = useState<string>("")
  const [selectedElements, setSelectedElements] = useState<string[]>([])
  const [selectedType, setSelectedType] = useState<string>("")
  const debouncedKeyword = useDebounce(keyword.trim(), DEBOUNCE_MS)
  const [state, setState] = useState<SearchState>(INITIAL_STATE)

  useEffect(() => {
    fetch('/api/stats')
      .then(res => res.json() as Promise<StatsData>)
      .then(data => setAllElements([...data.elements]))
      .catch(() => { /* ignore */ })
  }, [])

  const runSearch = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    const params: ListParams = {
      page: 1,
      limit: SEARCH_LIMIT,
      sort: "updated",
    }
    if (selectedType) params.type = selectedType
    if (selectedElements.length > 0) params.elements = [...selectedElements]
    if (debouncedKeyword) params.q = debouncedKeyword
    try {
      const result: PotentialListResult = await listPotentials(params)
      setState({
        potentials: result.potentials,
        total: result.total,
        loading: false,
        error: null,
      })
    } catch (err) {
      setState({
        potentials: [],
        total: 0,
        loading: false,
        error: err instanceof Error ? err.message : "搜索失败",
      })
    }
  }, [selectedType, selectedElements, debouncedKeyword])

  useEffect(() => {
    void runSearch()
  }, [runSearch])

  const toggleElement = useCallback((el: string) => {
    setSelectedElements(prev =>
      prev.includes(el) ? prev.filter(e => e !== el) : [...prev, el]
    )
  }, [])

  const resetFilters = useCallback(() => {
    setKeyword("")
    setSelectedElements([])
    setSelectedType("")
  }, [])

  return (
    <div className="space-y-6">
      {/* Browse link */}
      <div className="flex justify-end">
        <Link href="/browse" className="text-blue-400 hover:text-blue-300 text-sm">
          浏览全部
        </Link>
      </div>

      {/* Search form */}
      <div className="p-4 rounded-lg bg-gray-800 border border-gray-700 space-y-4">
        {/* Keyword */}
        <div>
          <label className="block text-xs uppercase tracking-wider text-gray-400 mb-1">关键词</label>
          <input
            type="text"
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            placeholder="输入关键词..."
            className="w-full px-3 py-1.5 rounded bg-gray-700 border border-gray-600 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
          />
        </div>

        {/* Elements */}
        <div>
          <label className="block text-xs uppercase tracking-wider text-gray-400 mb-1">元素筛选</label>
          <ElementFilter
            allElements={allElements}
            selected={selectedElements}
            onToggle={toggleElement}
          />
        </div>

        {/* Type + buttons */}
        <div className="flex items-end gap-3">
          <div className="flex-1">
            <label className="block text-xs uppercase tracking-wider text-gray-400 mb-1">函数形式</label>
            <select
              value={selectedType}
              onChange={e => setSelectedType(e.target.value)}
              className="w-full px-3 py-1.5 rounded bg-gray-700 border border-gray-600 text-sm text-gray-300 focus:outline-none focus:border-blue-500"
            >
              {TYPE_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <button
            onClick={() => void runSearch()}
            className="px-4 py-1.5 rounded bg-blue-600 border border-blue-500 text-sm text-white font-medium hover:bg-blue-700 transition"
          >
            搜索
          </button>
          <button
            onClick={resetFilters}
            className="px-4 py-1.5 rounded bg-gray-700 border border-gray-600 text-sm text-gray-300 hover:border-blue-500/50 transition"
          >
            重置
          </button>
        </div>
      </div>

      {/* Results */}
      <Spin spinning={state.loading} tip="加载中...">
        {state.error ? (
          <Empty description={`搜索失败：${state.error}`} />
        ) : state.potentials.length === 0 && !state.loading ? (
          <Empty description="未找到匹配的势函数" />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {state.potentials.map((p) => (
              <PotentialCard key={p.id} potential={p} />
            ))}
          </div>
        )}
      </Spin>

      {!state.error && state.total > 0 && (
        <Text type="secondary" className="block mt-6 text-center">
          共 {state.total} 条结果
        </Text>
      )}
    </div>
  )
}
