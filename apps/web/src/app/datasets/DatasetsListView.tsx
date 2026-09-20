"use client"

/**
 * /datasets — paginated list view (NFM-4991 IA-REFACTOR P2).
 *
 * Lightweight table of dataset rows: title, optional material name
 * (with expand), verified flag, measurement date, updated_at.
 * Click a row to open the detail page (/datasets/{id}) which surfaces
 * the §5.2 attribution block (NFM-4159).
 */

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Alert, Empty, Pagination, Spin, Table, Tag } from "antd"
import type { ColumnsType } from "antd/es/table"
import {
  listDatasets,
  type DatasetListItem,
  type DatasetListResult,
} from "@/lib/datasets-api"

interface ListState {
  items: DatasetListItem[]
  total: number
  page: number
  pages: number
  loading: boolean
  error: string | null
}

const INITIAL: ListState = {
  items: [],
  total: 0,
  page: 1,
  pages: 0,
  loading: true,
  error: null,
}

const PAGE_SIZE = 20

function formatDate(iso: string | null): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return "—"
  return d.toISOString().slice(0, 10)
}

export function DatasetsListView() {
  const [state, setState] = useState<ListState>(INITIAL)
  const [page, setPage] = useState(1)

  const load = useCallback(async (currentPage: number) => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    try {
      const result: DatasetListResult = await listDatasets({
        page: currentPage,
        perPage: PAGE_SIZE,
        // Material name joins are cheap on this dataset size and make
        // the list page readable without per-row follow-ups; the source
        // join is optional and skipped by default to keep anonymous
        // fetches to one query.
        expand: "material",
      })
      setState({
        items: result.items,
        total: result.total,
        page: result.page,
        pages: result.pages,
        loading: false,
        error: null,
      })
    } catch (err) {
      setState({
        items: [],
        total: 0,
        page: currentPage,
        pages: 0,
        loading: false,
        error: err instanceof Error ? err.message : "加载失败",
      })
    }
  }, [])

  useEffect(() => {
    void load(page)
  }, [page, load])

  const columns: ColumnsType<DatasetListItem> = [
    {
      title: "标题",
      dataIndex: "title",
      key: "title",
      render: (_, row) => (
        <Link
          href={`/datasets/${row.id}`}
          className="text-blue-400 hover:text-blue-300 hover:underline font-medium"
        >
          {row.title}
        </Link>
      ),
    },
    {
      title: "材料",
      dataIndex: "material_name",
      key: "material_name",
      render: (_, row) => {
        const name = row.material_name ?? row.material_id.slice(0, 8)
        return (
          <Link
            href={`/materials/${row.material_id}`}
            className="font-mono text-xs text-gray-300 hover:text-blue-300 hover:underline"
            title={row.material_name ?? "按 ID 查看材料"}
          >
            {name}
          </Link>
        )
      },
    },
    {
      title: "数据源",
      dataIndex: "source_id",
      key: "source_id",
      render: (_, row) =>
        row.source_id ? (
          <Link
            href={`/publications/${row.source_id}`}
            className="font-mono text-xs text-gray-300 hover:text-blue-300 hover:underline"
          >
            {row.source_title ?? row.source_id.slice(0, 8)}
          </Link>
        ) : (
          <span className="text-gray-500">—</span>
        ),
    },
    {
      title: "审核",
      dataIndex: "is_verified",
      key: "is_verified",
      width: 96,
      render: (verified: boolean) =>
        verified ? (
          <Tag color="green">已审核</Tag>
        ) : (
          <Tag color="default">未审核</Tag>
        ),
    },
    {
      title: "测量日期",
      dataIndex: "measurement_date",
      key: "measurement_date",
      width: 120,
      render: formatDate,
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      key: "updated_at",
      width: 120,
      render: formatDate,
    },
  ]

  return (
    <div className="space-y-4">
      <Alert
        type="info"
        showIcon
        message="数据集列表"
        description={
          <span>
            每个数据集归属一个材料和一个数据源（文献 / 实验报告等）。点击标题进入详情，
            可查看完整字段与 §5.2 attribution 块(NFM-4159)。
          </span>
        }
      />

      {state.error ? (
        <Alert
          type="error"
          showIcon
          message="加载失败"
          description={
            <div className="flex items-center gap-2">
              <span>{state.error}</span>
              <button
                type="button"
                onClick={() => void load(page)}
                className="px-2 py-0.5 rounded bg-gray-700 border border-red-500/50 text-gray-300 hover:border-red-400 transition"
              >
                重试
              </button>
            </div>
          }
        />
      ) : null}

      {state.loading && state.items.length === 0 ? (
        <div className="flex justify-center py-12">
          <Spin />
        </div>
      ) : state.items.length === 0 && !state.loading ? (
        <Empty description="暂无数据集" />
      ) : (
        <>
          <Table<DatasetListItem>
            rowKey="id"
            dataSource={state.items}
            columns={columns}
            pagination={false}
            loading={state.loading}
            size="middle"
            // Avoid the dark-on-dark row hover from antd default — keep
            // the dark theme intentional without bleaching text.
            className="text-gray-200"
          />
          {state.pages > 1 ? (
            <div className="flex justify-end pt-2">
              <Pagination
                current={state.page}
                pageSize={PAGE_SIZE}
                total={state.total}
                showSizeChanger={false}
                onChange={(next) => setPage(next)}
              />
            </div>
          ) : null}
        </>
      )}
    </div>
  )
}