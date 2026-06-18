"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Row, Col, Select, Pagination, Spin, Empty, Space, Typography } from "antd"
import type { PaginationProps } from "antd"
import { PotentialCard } from "@/components/potential/PotentialCard"
import {
  listPotentials,
  type ListParams,
  type PotentialSummary,
  type PotentialListResult,
} from "@/lib/potentials-api"

const { Title, Text } = Typography

const TYPE_OPTIONS = [
  { label: "全部类型", value: "" },
  { label: "EAM", value: "EAM" },
  { label: "MEAM", value: "MEAM" },
  { label: "MTP", value: "MTP" },
  { label: "ACE", value: "ACE" },
]

const PAGE_SIZE = 12

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
  const [typeFilter, setTypeFilter] = useState<string>("")
  const [state, setState] = useState<BrowseState>(INITIAL_STATE)

  const loadPage = useCallback(async (type: string, page: number) => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    const params: ListParams = {
      page,
      limit: PAGE_SIZE,
      sort: "updated",
    }
    if (type) params.type = type
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
    void loadPage(typeFilter, 1)
  }, [typeFilter, loadPage])

  const onPageChange: PaginationProps["onChange"] = (page) => {
    void loadPage(typeFilter, page)
  }

  const onTypeChange = (value: string) => {
    setTypeFilter(value)
  }

  return (
    <main style={{ maxWidth: 1200, margin: "0 auto", padding: "2rem 1.5rem" }}>
      <Space
        direction="vertical"
        size="middle"
        style={{ width: "100%", marginBottom: "1.5rem" }}
      >
        <Title level={2} style={{ margin: 0 }}>
          浏览势函数
        </Title>
        <Space wrap>
          <Text type="secondary">类型筛选：</Text>
          <Select
            value={typeFilter}
            onChange={onTypeChange}
            options={TYPE_OPTIONS}
            style={{ width: 160 }}
            placeholder="选择类型"
          />
          <Link href="/search">高级检索</Link>
        </Space>
      </Space>

      <Spin spinning={state.loading} tip="加载中...">
        {state.error ? (
          <Empty description={`加载失败：${state.error}`} />
        ) : state.potentials.length === 0 && !state.loading ? (
          <Empty description="暂无势函数数据" />
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
        <Pagination
          current={state.page}
          total={state.total}
          pageSize={PAGE_SIZE}
          onChange={onPageChange}
          showSizeChanger={false}
          showTotal={(total) => `共 ${total} 条`}
          style={{ marginTop: "2rem", textAlign: "center" }}
        />
      )}
    </main>
  )
}
