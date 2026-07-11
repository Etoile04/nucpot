"use client"

import { useState, useCallback } from "react"
import { Typography } from "antd"
import { ragApi } from "@/lib/rag-api"
import type { RagCitation } from "@/lib/rag-api"
import { SemanticSearchResults } from "./SemanticSearchResults"

const { Text } = Typography

const TOP_K = 5

interface RagSearchState {
  readonly answer: string
  readonly citations: readonly RagCitation[]
  readonly loading: boolean
  readonly error: string | null
  readonly hasSearched: boolean
}

const INITIAL_STATE: RagSearchState = {
  answer: "",
  citations: [],
  loading: false,
  error: null,
  hasSearched: false,
}

interface RagSearchViewProps {
  readonly initialQuery?: string
}

export function RagSearchView({ initialQuery = "" }: RagSearchViewProps) {
  const [query, setQuery] = useState(initialQuery)
  const [state, setState] = useState<RagSearchState>(INITIAL_STATE)

  const handleSearch = useCallback(async () => {
    const trimmed = query.trim()
    if (!trimmed) {
      return
    }

    setState((prev) => ({
      ...prev,
      loading: true,
      error: null,
      hasSearched: true,
    }))

    try {
      const response = await ragApi.query({
        query: trimmed,
        topK: TOP_K,
      })

      setState({
        answer: response.answer,
        citations: [...response.citations],
        loading: false,
        error: null,
        hasSearched: true,
      })
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "语义检索失败，请重试"
      setState((prev) => ({
        ...prev,
        loading: false,
        error: message,
        hasSearched: true,
      }))
    }
  }, [query])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") {
        e.preventDefault()
        void handleSearch()
      }
    },
    [handleSearch],
  )

  return (
    <div className="space-y-6">
      {/* Search input */}
      <div className="p-4 rounded-lg bg-gray-800 border border-gray-700">
        <label className="block text-xs uppercase tracking-wider text-gray-400 mb-1">
          语义查询
        </label>
        <div className="flex gap-3">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="描述您想了解的核材料属性或关系..."
            className="flex-1 px-3 py-1.5 rounded bg-gray-700 border border-gray-600 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
            disabled={state.loading}
          />
          <button
            onClick={() => void handleSearch()}
            disabled={state.loading || query.trim().length === 0}
            className="px-4 py-1.5 rounded bg-blue-600 border border-blue-500 text-sm text-white font-medium hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {state.loading ? "检索中..." : "语义检索"}
          </button>
        </div>
        <Text type="secondary" className="block mt-2 text-xs">
          输入自然语言问题，AI 将从知识图谱中检索相关内容并生成回答
        </Text>
      </div>

      {/* Results */}
      <SemanticSearchResults
        answer={state.answer}
        citations={state.citations}
        loading={state.loading}
        error={state.error}
      />
    </div>
  )
}
