"use client"

/**
 * /datasets — paginated list view (NFM-4991 IA-REFACTOR P2).
 *
 * Lightweight table of dataset rows: title, material name, source
 * title, verified flag, measurement date, updated_at. Click a row to
 * open the detail page (/datasets/{id}).
 *
 * NFM-5321 (Visual-Truth Gate retro-gate fixes):
 *   - D1: user-facing copy only — no internal ticket ids or paths.
 *   - D3: the 数据源 column resolves source titles via expand=source
 *     and falls back to 「—」 instead of a truncated UUID fragment.
 *   - D4: page shell mirrors MaterialsListView (shared chrome + antd
 *     components, standard max-w-[1200px] container) instead of a
 *     page-local gradient wrapper.
 *
 * NFM-5330 (390px viewport clip, out-of-scope finding of the NFM-5321
 * re-verdict): the fixed-width trailing columns (96/120/120) used to
 * bleed past the viewport with `overflow: visible` — 审核/测量日期/更新时间
 * were unreachable and the 标题 column wrapped up to four lines. The
 * table now sets `scroll={{ x: 840 }}` (antd's scroll container, the
 * same pattern as MaterialsListView and 14 other tables), wraps in
 * TableScrollFade for a swipe affordance, and keeps the three text
 * columns single-line via `ellipsis` + native title tooltip.
 */

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { Alert, Button, Empty, Pagination, Spin, Table, Tag, Typography } from "antd"
import type { ColumnsType } from "antd/es/table"
import { formatDate } from "@/lib/format-date"
import { TableScrollFade } from "@/components/tables/TableScrollFade"
import {
  listDatasets,
  type DatasetListItem,
  type DatasetListResult,
} from "@/lib/datasets-api"

const { Title, Text } = Typography

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

/**
 * NFM-5330 — minimum table width below which antd switches to its
 * horizontal scroll container. Fixed trailing columns take 336px
 * (96 + 120 + 120); 840 leaves the three text columns ~168px each,
 * enough for the truncated-title tooltips to stay useful. On viewports
 * wider than this (desktop), rc-table's `min-width: 100%` keeps the
 * table stretched to the container — same single-block layout as
 * before, no scroll, no fades.
 */
const TABLE_MIN_WIDTH_PX = 840

export function DatasetsListView() {
  const [state, setState] = useState<ListState>(INITIAL)
  const [page, setPage] = useState(1)

  const load = useCallback(async (currentPage: number) => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    try {
      const result: DatasetListResult = await listDatasets({
        page: currentPage,
        perPage: PAGE_SIZE,
        // Material + source name joins make both columns render
        // human-readable names; the list endpoint keeps them opt-in so
        // anonymous fetches that don't need names stay single-query
        // (NFM-5321 D3 added the source join).
        expand: "material,source",
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
      // NFM-5330: single line + ellipsis instead of wrapping to four
      // lines at 390px; the anchor's title attr keeps the full text
      // available as a native tooltip.
      ellipsis: true,
      render: (_, row) => (
        <Link
          href={`/datasets/${row.id}`}
          className="text-blue-400 hover:text-blue-300 hover:underline font-medium"
          title={row.title}
        >
          {row.title}
        </Link>
      ),
    },
    {
      title: "材料",
      dataIndex: "material_name",
      key: "material_name",
      ellipsis: true,
      render: (_, row) => {
        const name = row.material_name ?? row.material_id.slice(0, 8)
        return (
          <Link
            href={`/materials/${row.material_id}`}
            className="hover:underline"
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
      ellipsis: true,
      // NFM-5321 D3: expand=source resolves the title; an unresolved
      // title renders 「—」 (the table's empty-value convention) rather
      // than a truncated UUID fragment.
      render: (_, row) =>
        row.source_id && row.source_title ? (
          <Link
            href={`/publications/${row.source_id}`}
            className="hover:underline"
            title="查看数据源文献"
          >
            {row.source_title}
          </Link>
        ) : (
          <Text type="secondary">—</Text>
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
      render: (iso: string | null) => formatDate(iso),
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      key: "updated_at",
      width: 120,
      render: (iso: string | null) => formatDate(iso),
    },
  ]

  return (
    <div className="max-w-[1200px] mx-auto px-6 py-8">
      <Title level={2}>数据集</Title>
      <Text type="secondary">
        每个数据集来自一种材料与一个数据源（文献、实验报告或数据库）的一组测量。点击标题查看数据详情与数据归属说明。
      </Text>

      <div className="mt-6 space-y-4">
        {state.error ? (
          <Alert
            type="error"
            showIcon
            message="加载失败"
            description={
              <div className="flex items-center gap-2">
                <span>{state.error}</span>
                <Button size="small" onClick={() => void load(page)}>
                  重试
                </Button>
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
            <TableScrollFade>
              <Table<DatasetListItem>
                rowKey="id"
                dataSource={state.items}
                columns={columns}
                pagination={false}
                loading={state.loading}
                size="middle"
                scroll={{ x: TABLE_MIN_WIDTH_PX }}
              />
            </TableScrollFade>
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
    </div>
  )
}
