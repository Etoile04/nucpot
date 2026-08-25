/**
 * GapReviewQueuePage -- orchestrates the Gap Review queue.
 *
 * Uses TanStack Query for data fetching, URL search params for filters,
 * and composes GapReviewFilters + Ant Design Table + ConfidenceMeter.
 *
 * Spec: NFM-3704
 */

"use client"

import { useCallback, useMemo, useState } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { Table, Tag, Button, Space, Card, Empty, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { GapReviewFilters } from './GapReviewFilters'
import { ConfidenceMeter } from './ConfidenceMeter'
import { GapCandidateDrawer } from './GapCandidateDrawer'
import { BulkActionToolbar } from './BulkActionToolbar'
import ErrorEmptyState from '@/components/v4-extraction/error-empty-state'
import { fetchGapCandidates, PAGE_SIZE } from '@/lib/reference-gaps/gap-candidates-api'
import { DEFAULT_FILTERS } from '@/lib/reference-gaps/gap-candidates'
import type { GapCandidate, GapCandidateFilters } from '@/lib/reference-gaps/gap-candidates'
import type { GapCandidate as BulkGapCandidate } from '@/lib/gap-decisions/types'
import type { GapCandidate as DrawerGapCandidate } from '@/lib/reference-gaps/types'

function toBulkItem(c: GapCandidate): BulkGapCandidate {
  return { candidate_id: c.id, confidence: c.confidence }
}

function toDrawerCandidate(c: GapCandidate): DrawerGapCandidate {
  return {
    ...c,
    source_document: c.source_doc,
    match_spans: c.matched_spans,
    suggested_properties: [],
  }
}

function parseFilters(searchParams: URLSearchParams): GapCandidateFilters {
  return {
    confidence_min: searchParams.has('confidence_min') ? Number(searchParams.get('confidence_min')) : undefined,
    confidence_max: searchParams.has('confidence_max') ? Number(searchParams.get('confidence_max')) : undefined,
    entity_type: searchParams.get('entity_type') ?? undefined,
    source_doc: searchParams.get('source_doc') ?? undefined,
    decision_status: searchParams.get('decision_status') ?? undefined,
  }
}

function hasActiveFilters(filters: GapCandidateFilters): boolean {
  return Object.values(filters).some((v) => v !== undefined && v !== '')
}

const STATUS_TAG: Record<string, { color: string; label: string }> = {
  pending: { color: 'gold', label: 'Pending' },
  approved: { color: 'green', label: 'Approved' },
  rejected: { color: 'red', label: 'Rejected' },
  skipped: { color: 'default', label: 'Skipped' },
}

export function GapReviewQueuePage() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const [page, setPage] = useState(1)
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [drawerCandidate, setDrawerCandidate] = useState<DrawerGapCandidate | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [messageApi, contextHolder] = message.useMessage()

  const filters = useMemo(() => parseFilters(searchParams), [searchParams])

  const queryKey = useMemo(
    () => ['gap-candidates', filters, page],
    [filters, page],
  )

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey,
    queryFn: () => fetchGapCandidates(filters, page, PAGE_SIZE),
  })

  const isFiltered = hasActiveFilters(filters)

  const handleFilterChange = useCallback(
    (next: GapCandidateFilters) => {
      const params = new URLSearchParams()
      if (next.confidence_min !== undefined) params.set('confidence_min', String(next.confidence_min))
      if (next.confidence_max !== undefined) params.set('confidence_max', String(next.confidence_max))
      if (next.entity_type) params.set('entity_type', next.entity_type)
      if (next.source_doc) params.set('source_doc', next.source_doc)
      if (next.decision_status) params.set('decision_status', next.decision_status)
      router.replace(`/admin/gap-review/queue?${params.toString()}`)
      setPage(1)
    },
    [router],
  )

  const handlePageChange = useCallback((newPage: number) => {
    setPage(newPage)
  }, [])

  const candidates = data?.items ?? []

  const handleRowClick = useCallback((record: GapCandidate) => {
    setDrawerCandidate(toDrawerCandidate(record))
    setDrawerOpen(true)
  }, [])

  const handleDrawerClose = useCallback(() => {
    setDrawerOpen(false)
    setDrawerCandidate(null)
  }, [])

  const handleDrawerDecision = useCallback(
    (_candidateId: string, _decision: string) => {
      setDrawerOpen(false)
      setDrawerCandidate(null)
      setSelectedRowKeys([])
      refetch()
    },
    [refetch],
  )

  const handleBulkSuccess = useCallback(() => {
    setSelectedRowKeys([])
    refetch()
    messageApi.success('Bulk decision submitted')
  }, [refetch, messageApi])

  const handleBulkError = useCallback(
    (msg: string) => {
      messageApi.error(msg)
    },
    [messageApi],
  )

  const rowSelection = useMemo(
    () => ({
      selectedRowKeys,
      onChange: (keys: React.Key[]) => setSelectedRowKeys(keys),
    }),
    [selectedRowKeys],
  )

  const columns = useMemo(
    () => [
      {
        title: 'Entity Name',
        dataIndex: 'entity_name',
        key: 'entity_name',
        width: 200,
        ellipsis: true,
      },
      {
        title: 'Type',
        dataIndex: 'entity_type',
        key: 'entity_type',
        width: 120,
      },
      {
        title: 'Confidence',
        dataIndex: 'confidence',
        key: 'confidence',
        width: 180,
        render: (value: number) => <ConfidenceMeter value={value} />,
      },
      {
        title: 'Source Doc',
        dataIndex: 'source_doc',
        key: 'source_doc',
        width: 160,
        ellipsis: true,
      },
      {
        title: 'Status',
        dataIndex: 'decision_status',
        key: 'decision_status',
        width: 120,
        render: (status: string) => {
          const cfg = STATUS_TAG[status]
          return cfg ? <Tag color={cfg.color}>{cfg.label}</Tag> : <span>{status}</span>
        },
      },
      {
        title: 'Created',
        dataIndex: 'created_at',
        key: 'created_at',
        width: 160,
        render: (iso: string) => new Date(iso).toLocaleString(),
      },
    ],
    [],
  )

  return (
    <div style={{ padding: 24 }}>
      {contextHolder}
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Card
          title="Gap Review Queue"
          extra={
            <Button
              icon={<ReloadOutlined />}
              onClick={() => refetch()}
              loading={isLoading}
            >
              Refresh
            </Button>
          }
        >
          <GapReviewFilters
            filters={filters}
            onFilterChange={handleFilterChange}
          />
        </Card>

        {isError && (
          <ErrorEmptyState
            title="Failed to load gap candidates"
            description={error instanceof Error ? error.message : undefined}
            onRetry={() => refetch()}
          />
        )}

        {selectedRowKeys.length > 0 && (
          <BulkActionToolbar
            selectedItems={candidates.filter((c) => selectedRowKeys.includes(c.id)).map(toBulkItem)}
            confidenceThreshold={0.7}
            onSuccess={handleBulkSuccess}
            onError={handleBulkError}
          />
        )}

        {!isError && !isLoading && data && data.items.length === 0 && !isFiltered && (
          <Empty
            description={
              <span>
                No gap candidates yet.{' '}
                <a href="/admin/reference-data">Go to Reference Data</a>
              </span>
            }
          />
        )}

        {!isError && !isLoading && data && data.items.length === 0 && isFiltered && (
          <Empty
            description={
              <span>
                No candidates match your filters.{' '}
                <Button type="link" onClick={() => handleFilterChange({ ...DEFAULT_FILTERS })}>
                  Clear filters
                </Button>
              </span>
            }
          />
        )}

        <Table
          columns={columns}
          dataSource={candidates}
          rowKey="id"
          rowSelection={rowSelection}
          onRow={(record) => ({ onClick: () => handleRowClick(record), style: { cursor: 'pointer' } })}
          loading={isLoading}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total: data?.total ?? 0,
            showSizeChanger: false,
            showTotal: (total) => `Total ${total}`,
            onChange: handlePageChange,
          }}
          scroll={{ x: 1000 }}
          size="middle"
        />

        <GapCandidateDrawer
          candidate={drawerCandidate}
          open={drawerOpen}
          onClose={handleDrawerClose}
          onDecision={handleDrawerDecision}
        />
      </Space>
    </div>
  )
}
