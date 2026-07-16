"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Typography, Spin } from "antd"
import {
  MaterialPropertyTable,
  type TableChangeParams,
  type SortField,
} from "@/components/materials/MaterialPropertyTable"
import {
  getMaterialProperties,
  getMaterial,
  type MaterialProperty,
  type MaterialSummary,
} from "@/lib/materials-api"

const { Title, Text } = Typography

const DEFAULT_PAGE_SIZE = 50

// ── Types ──────────────────────────────────────────────────────────────

interface MaterialPropertiesViewProps {
  readonly materialId: string
}

interface ViewState {
  readonly properties: ReadonlyArray<MaterialProperty>
  readonly total: number
  readonly page: number
  readonly pageSize: number
  readonly sortField: SortField | null
  readonly sortOrder: "asc" | "desc" | null
  readonly filterText: string
  readonly loading: boolean
  readonly error: string | null
  readonly material: MaterialSummary | null
}

const INITIAL_STATE: ViewState = {
  properties: [],
  total: 0,
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
  sortField: null,
  sortOrder: null,
  filterText: "",
  loading: true,
  error: null,
  material: null,
}

// ── Component ──────────────────────────────────────────────────────────

export function MaterialPropertiesView({
  materialId,
}: MaterialPropertiesViewProps) {
  const [state, setState] = useState<ViewState>(INITIAL_STATE)

  const fetchData = useCallback(
    async (page: number, pageSize: number, sortField: SortField | null, sortOrder: "asc" | "desc" | null, filterText: string) => {
      setState((prev) => ({ ...prev, loading: true, error: null }))

      // Use Promise.allSettled so a partial failure (e.g. properties 500
      // while material 200) does not blank the header. Promise.all used to
      // short-circuit and discard the successful material result.
      const [materialResult, propsResult] = await Promise.allSettled([
        getMaterial(materialId),
        getMaterialProperties(materialId, {
          page,
          limit: pageSize,
          sort: sortField ?? undefined,
          order: sortOrder ?? undefined,
          filter: filterText.trim() || undefined,
        }),
      ])

      const material =
        materialResult.status === "fulfilled" ? materialResult.value : null
      const properties =
        propsResult.status === "fulfilled"
          ? propsResult.value
          : { data: [] as ReadonlyArray<MaterialProperty>, meta: { total: 0, page: 1, limit: pageSize } }
      const error =
        propsResult.status === "rejected"
          ? propsResult.reason instanceof Error
            ? propsResult.reason.message
            : "加载失败"
          : null

      setState((prev) => ({
        ...prev,
        material,
        properties: properties.data,
        total: properties.meta.total,
        loading: false,
        error,
      }))
    },
    [materialId],
  )

  useEffect(() => {
    void fetchData(state.page, state.pageSize, state.sortField, state.sortOrder, state.filterText)
  }, [fetchData, state.page, state.pageSize, state.sortField, state.sortOrder, state.filterText])

  const handlePageChange = useCallback(
    (params: TableChangeParams) => {
      setState((prev) => ({
        ...prev,
        page: params.page,
        pageSize: params.pageSize,
        sortField: params.sortField,
        sortOrder: params.sortOrder,
      }))
    },
    [],
  )

  const handleFilterChange = useCallback(
    (filter: string) => {
      setState((prev) => ({
        ...prev,
        filterText: filter,
        page: 1,
      }))
    },
    [],
  )

  return (
    <main className="max-w-[1200px] mx-auto px-4 sm:px-6 py-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 mb-6">
        <div>
          <Title level={2} className="!m-0 text-white">
            {state.material?.name ?? "材料属性"}
          </Title>
          <Text type="secondary">
            {state.material?.formula
              ? `化学式：${state.material.formula}`
              : `材料 ID：${materialId}`}
          </Text>
        </div>
        <Link
          href="/browse"
          className="text-blue-400 hover:text-blue-300 text-sm"
        >
          返回浏览
        </Link>
      </div>

      {/* Loading state */}
      {state.loading && !state.properties.length && (
        <div className="flex items-center justify-center py-20">
          <Spin tip="加载中..."><div className="h-8" /></Spin>
        </div>
      )}

      {/* Table */}
      {!state.loading && (
        <MaterialPropertyTable
          data={state.properties}
          total={state.total}
          error={state.error}
          page={state.page}
          pageSize={state.pageSize}
          sortField={state.sortField}
          sortOrder={state.sortOrder}
          filterText={state.filterText}
          onPageChange={handlePageChange}
          onFilterChange={handleFilterChange}
        />
      )}
    </main>
  )
}
