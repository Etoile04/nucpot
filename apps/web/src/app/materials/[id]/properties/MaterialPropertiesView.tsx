"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Typography, Spin } from "antd"
import { MaterialPropertyTable } from "@/components/materials/MaterialPropertyTable"
import {
  getMaterialProperties,
  getMaterial,
  type MaterialProperty,
  type MaterialSummary,
} from "@/lib/materials-api"

const { Title, Text } = Typography

// ── Types ──────────────────────────────────────────────────────────────

interface MaterialPropertiesViewProps {
  readonly materialId: string
}

interface ViewState {
  readonly properties: ReadonlyArray<MaterialProperty>
  readonly total: number
  readonly loading: boolean
  readonly error: string | null
  readonly material: MaterialSummary | null
}

const INITIAL_STATE: ViewState = {
  properties: [],
  total: 0,
  loading: true,
  error: null,
  material: null,
}

// ── Component ──────────────────────────────────────────────────────────

export function MaterialPropertiesView({
  materialId,
}: MaterialPropertiesViewProps) {
  const [state, setState] = useState<ViewState>(INITIAL_STATE)

  const fetchData = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }))

    try {
      const [material, propsResult] = await Promise.all([
        getMaterial(materialId).catch(() => null),
        getMaterialProperties(materialId, {
          page: 1,
          limit: 50,
          sort: "name",
          order: "asc",
        }),
      ])

      setState({
        material,
        properties: propsResult.data,
        total: propsResult.meta.total,
        loading: false,
        error: null,
      })
    } catch (err) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: err instanceof Error ? err.message : "加载失败",
      }))
    }
  }, [materialId])

  useEffect(() => {
    void fetchData()
  }, [fetchData])

  return (
    <main className="max-w-[1200px] mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
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
          <Spin tip="加载中..." />
        </div>
      )}

      {/* Table */}
      {!state.loading && (
        <MaterialPropertyTable
          data={state.properties}
          total={state.total}
          error={state.error}
        />
      )}
    </main>
  )
}
