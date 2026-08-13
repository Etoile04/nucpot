"use client"

import { useCallback, useEffect, useState } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { Spin, Empty, Button, Typography } from "antd"
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons"
import { useDebounce } from "@/components/potential/useDebounce"
import { useReducedMotion } from "@/components/graph/useReducedMotion"
import {
  fetchKgSearch,
  KG_NODE_TYPES,
  type KgNodeType,
  type KgSearchItem,
  type KgSearchParams,
} from "@/lib/kg-search-api"

const { Title, Text } = Typography

const DEBOUNCE_MS = 300
const PAGE_SIZE = 20

const TYPE_OPTIONS = [
  { label: "All Types", value: "" },
  ...KG_NODE_TYPES.map((t) => ({ label: t, value: t })),
]

/**
 * Compose Tailwind class lists, dropping falsy entries so conditional
 * utilities (e.g. motion classes suppressed under `prefers-reduced-motion`)
 * can be omitted cleanly from the rendered className.
 */
function cx(
  ...parts: Array<string | false | undefined | null>
): string {
  return parts.filter((p): p is string => Boolean(p)).join(" ")
}

// Color palette for node type badges
const TYPE_COLORS: Record<string, string> = {
  Material: "bg-blue-500/20 text-blue-300 border-blue-500/30",
  Property: "bg-green-500/20 text-green-300 border-green-500/30",
  Experiment: "bg-purple-500/20 text-purple-300 border-purple-500/30",
  Condition: "bg-amber-500/20 text-amber-300 border-amber-500/30",
  Publication: "bg-rose-500/20 text-rose-300 border-rose-500/30",
}

function confidenceLabel(score: number): string {
  return `${Math.round(score * 100)}%`
}

function getExcerpt(item: KgSearchItem): string {
  const values = Object.values(item.properties)
  if (values.length === 0) {
    if (item.aliases.length > 0) return item.aliases[0] as string
    return ""
  }
  const first = String(values[0])
  return first.length > 120 ? `${first.slice(0, 120)}…` : first
}

interface SearchState {
  readonly results: readonly KgSearchItem[]
  readonly total: number
  readonly loading: boolean
  readonly error: string | null
}

const INITIAL_STATE: SearchState = {
  results: [],
  total: 0,
  loading: false,
  error: null,
}

export function KgSearchContent() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const prefersReducedMotion = useReducedMotion()

  const urlQuery = searchParams.get("q") ?? ""
  const rawType = searchParams.get("type") ?? ""
  const urlType = KG_NODE_TYPES.includes(rawType as KgNodeType) ? rawType : ""

  const [keyword, setKeyword] = useState(urlQuery)
  const [selectedType, setSelectedType] = useState(urlType)
  const [state, setState] = useState<SearchState>(INITIAL_STATE)

  const debouncedQuery = useDebounce(keyword.trim(), DEBOUNCE_MS)

  const syncUrl = useCallback(
    (q: string, type: string) => {
      const params = new URLSearchParams()
      if (q) params.set("q", q)
      if (type) params.set("type", type)
      const qs = params.toString()
      router.replace(`/kg/search${qs ? `?${qs}` : ""}`, { scroll: false })
    },
    [router],
  )

  // Sync URL params to state on navigation
  useEffect(() => {
    setKeyword(urlQuery)
    setSelectedType(urlType)
  }, [urlQuery, urlType])

  // Fetch when debounced query or type changes
  useEffect(() => {
    if (!debouncedQuery && !selectedType) {
      setState({ results: [], total: 0, loading: false, error: null })
      return
    }

    let cancelled = false
    setState((prev) => ({ ...prev, loading: true, error: null }))

    const params: KgSearchParams = {
      q: debouncedQuery || undefined,
      type: selectedType || undefined,
      limit: PAGE_SIZE,
      offset: 0,
    }

    fetchKgSearch(params)
      .then((data) => {
        if (!cancelled) {
          setState({
            results: data.items,
            total: data.total,
            loading: false,
            error: null,
          })
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : "Search failed"
          setState((prev) => ({ ...prev, loading: false, error: message }))
        }
      })

    return () => {
      cancelled = true
    }
  }, [debouncedQuery, selectedType])

  const handleKeywordChange = useCallback(
    (value: string) => {
      setKeyword(value)
      syncUrl(value.trim(), selectedType)
    },
    [selectedType, syncUrl],
  )

  const handleTypeChange = useCallback(
    (value: string) => {
      setSelectedType(value)
      syncUrl(keyword.trim(), value)
    },
    [keyword, syncUrl],
  )

  const handleResultClick = useCallback(
    (item: KgSearchItem) => {
      router.push(`/kg/nodes/${item.node_type}/${item.id}`)
    },
    [router],
  )

  const handleRetry = useCallback(() => {
    const params: KgSearchParams = {
      q: debouncedQuery || undefined,
      type: selectedType || undefined,
      limit: PAGE_SIZE,
    }
    setState((prev) => ({ ...prev, loading: true, error: null }))
    fetchKgSearch(params)
      .then((data) => {
        setState({
          results: data.items,
          total: data.total,
          loading: false,
          error: null,
        })
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : "Search failed"
        setState((prev) => ({ ...prev, loading: false, error: message }))
      })
  }, [debouncedQuery, selectedType])

  const inputClassName = cx(
    "w-full pl-10 pr-4 py-2.5 rounded-lg bg-[var(--bg-elevated,#1a1a2e)] border border-[var(--border-color,#2d2d44)] text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50",
    !prefersReducedMotion && "transition-shadow",
  )

  const selectClassName = cx(
    "px-4 py-2.5 rounded-lg bg-[var(--bg-elevated,#1a1a2e)] border border-[var(--border-color,#2d2d44)] text-white focus:outline-none focus:ring-2 focus:ring-blue-500/50 min-w-[160px]",
    !prefersReducedMotion && "transition-shadow",
  )

  const resultButtonClassName = cx(
    "w-full text-left p-4 rounded-lg bg-[var(--bg-elevated,#1a1a2e)] border border-[var(--border-color,#2d2d44)] hover:border-blue-500/40 hover:bg-[var(--bg-elevated-hover,#22223a)] cursor-pointer group",
    !prefersReducedMotion && "transition-all duration-150",
  )

  const resultLabelClassName = cx(
    "text-white font-medium group-hover:text-blue-300 truncate",
    !prefersReducedMotion && "transition-colors",
  )

  return (
    <main className="max-w-[1200px] mx-auto px-6 py-8">
      {/* Header */}
      <div className="mb-6">
        <Title level={2} className="!m-0 text-white">
          Knowledge Graph Search
        </Title>
        <Text type="secondary">
          Search across materials, properties, experiments, conditions, and
          publications in the nuclear fuel knowledge graph.
        </Text>
      </div>

      {/* Search bar + type filter */}
      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <div className="relative flex-1">
          <SearchOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            value={keyword}
            onChange={(e) => handleKeywordChange(e.target.value)}
            placeholder="Search nodes by label or alias…"
            className={inputClassName}
          />
        </div>
        <select
          value={selectedType}
          onChange={(e) => handleTypeChange(e.target.value)}
          className={selectClassName}
          aria-label="Filter by node type"
        >
          {TYPE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {/* Loading */}
      {state.loading && (
        <div className="flex justify-center items-center min-h-[200px]">
          <Spin tip="Searching…"><div /></Spin>
        </div>
      )}

      {/* Error */}
      {!state.loading && state.error && (
        <div className="flex flex-col items-center justify-center min-h-[200px] gap-4">
          <Text type="danger">{state.error}</Text>
          <Button icon={<ReloadOutlined />} onClick={handleRetry}>
            Retry
          </Button>
        </div>
      )}

      {/* Empty — no query entered */}
      {!state.loading &&
        !state.error &&
        !debouncedQuery &&
        !selectedType && (
          <div className="flex flex-col items-center justify-center min-h-[200px]">
            <Text type="secondary" className="text-lg">
              Enter a search query or select a type to begin.
            </Text>
          </div>
        )}

      {/* Empty — query returned no results */}
      {!state.loading &&
        !state.error &&
        (debouncedQuery || selectedType) &&
        state.results.length === 0 && (
          <div className="flex flex-col items-center justify-center min-h-[200px]">
            <Empty
              description={
                <Text type="secondary">
                  No results found for &ldquo;
                  {debouncedQuery || selectedType}
                  &rdquo;
                </Text>
              }
            />
          </div>
        )}

      {/* Results */}
      {!state.loading && !state.error && state.results.length > 0 && (
        <div className="space-y-3">
          <Text type="secondary" className="block mb-2">
            {state.total} result{state.total !== 1 ? "s" : ""} found
          </Text>
          <ul className="space-y-2">
            {state.results.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => handleResultClick(item)}
                  className={resultButtonClassName}
                >
                  <div className="flex flex-wrap items-center gap-2 mb-1">
                    {/* Type badge */}
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${
                        TYPE_COLORS[item.node_type] ??
                        "bg-gray-500/20 text-gray-300 border-gray-500/30"
                      }`}
                    >
                      {item.node_type}
                    </span>
                    {/* Confidence */}
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-700/50 text-gray-400 border border-gray-600/30">
                      {confidenceLabel(item.confidence)}
                    </span>
                  </div>
                  <div className={resultLabelClassName}>{item.label}</div>
                  {getExcerpt(item) && (
                    <div className="text-sm text-gray-400 mt-1 line-clamp-2">
                      {getExcerpt(item)}
                    </div>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </main>
  )
}