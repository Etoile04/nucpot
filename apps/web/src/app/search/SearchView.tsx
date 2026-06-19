"use client"

import { useCallback, useEffect, useState } from "react"
import { Row, Col, Spin, Empty, Space, Typography } from "antd"
import { SearchFilters } from "@/components/potential/SearchFilters"
import { PotentialCard } from "@/components/potential/PotentialCard"
import { useDebounce } from "@/components/potential/useDebounce"
import {
  listPotentials,
  type ListParams,
  type PotentialSummary,
  type PotentialListResult,
} from "@/lib/potentials-api"

const { Title, Text } = Typography

const DEBOUNCE_MS = 300
const SEARCH_LIMIT = 24

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
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined)
  const [elements, setElements] = useState<string[]>([])
  const [query, setQuery] = useState<string>("")
  const debouncedQuery = useDebounce(query.trim(), DEBOUNCE_MS)
  const [state, setState] = useState<SearchState>(INITIAL_STATE)

  const runSearch = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    const params: ListParams = {
      page: 1,
      limit: SEARCH_LIMIT,
      sort: "updated",
    }
    if (typeFilter) params.type = typeFilter
    if (elements.length > 0) params.elements = [...elements]
    if (debouncedQuery) params.q = debouncedQuery
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
  }, [typeFilter, elements, debouncedQuery])

  useEffect(() => {
    void runSearch()
  }, [runSearch])

  return (
    <main className="max-w-[1200px] mx-auto px-6 py-8">
      <Space
        direction="vertical"
        size="middle"
        className="w-full mb-6"
      >
        <Title level={2} className="!m-0 text-white">
          高级检索
        </Title>
        <Text type="secondary">按类型、元素或关键字检索势函数库</Text>
        <SearchFilters
          type={typeFilter}
          onTypeChange={setTypeFilter}
          elements={elements}
          onElementsChange={setElements}
          query={query}
          onQueryChange={setQuery}
        />
      </Space>

      <Spin spinning={state.loading} tip="加载中...">
        {state.error ? (
          <Empty description={`搜索失败：${state.error}`} />
        ) : state.potentials.length === 0 && !state.loading ? (
          <Empty description="未找到匹配的势函数" />
        ) : (
          <Row gutter={[16, 16]}>
            {state.potentials.map((p) => (
              <Col key={p.id} xs={24} sm={12} md={8} lg={6}>
                <PotentialCard potential={p} />
              </Col>
            ))}
          </Row>
        )}
      </Spin>

      {!state.error && state.total > 0 && (
        <Text type="secondary" className="block mt-6 text-center">
          共 {state.total} 条结果
        </Text>
      )}
    </main>
  )
}
