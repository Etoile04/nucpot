/**
 * KG Review Page — review queue for knowledge graph entities.
 *
 * Route: /review/kg
 * Spec: NFM-848 §3.7
 *
 * Auth is handled by review/layout.tsx (BlogAuthGuard wrapper).
 */

'use client'

import { useState, useEffect, useCallback } from 'react'
import {
  ReviewQueueTable,
  type ReviewItem,
} from '@/components/review/ReviewQueueTable'
import {
  getKgReviewQueue,
  batchKgAction,
} from '@/lib/review-api'

// ── Constants ──────────────────────────────────────────────────────────

const STATUS_FILTER_OPTIONS = [
  { value: 'pending', label: '待审核' },
  { value: 'approved', label: '已通过' },
  { value: 'rejected', label: '已拒绝' },
] as const

const PAGE_SIZE = 20

// ── Status Bar Component ──────────────────────────────────────────────

interface StatusBarProps {
  readonly pending: number
  readonly approved: number
  readonly rejected: number
}

function StatusBar({ pending, approved, rejected }: StatusBarProps) {
  return (
    <div className="flex items-center gap-4 text-sm text-gray-400 px-1">
      <span>
        待审核: <strong className="text-amber-400">{pending}</strong>
      </span>
      <span className="text-gray-600">·</span>
      <span>
        已通过: <strong className="text-emerald-400">{approved}</strong>
      </span>
      <span className="text-gray-600">·</span>
      <span>
        已拒绝: <strong className="text-red-400">{rejected}</strong>
      </span>
    </div>
  )
}

// ── Page Header Component ─────────────────────────────────────────────

interface PageHeaderProps {
  readonly title: string
  readonly filterValue: string
  readonly onFilterChange: (value: string) => void
  readonly onRefresh: () => void
}

function PageHeader({ title, filterValue, onFilterChange, onRefresh }: PageHeaderProps) {
  return (
    <div className="flex items-center justify-between mb-6">
      <h1 className="text-2xl font-bold text-gray-100">{title}</h1>
      <div className="flex items-center gap-3">
        <select
          value={filterValue}
          onChange={(e) => onFilterChange(e.target.value)}
          className="px-3 py-1.5 rounded-lg bg-gray-700 border border-gray-600 text-gray-200 text-sm focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500"
          aria-label="筛选状态"
        >
          <option value="all">全部</option>
          {STATUS_FILTER_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={onRefresh}
          className="px-3 py-1.5 rounded-lg bg-gray-700 border border-gray-600 text-gray-200 text-sm hover:bg-gray-600 transition-colors"
          aria-label="刷新"
        >
          刷新
        </button>
      </div>
    </div>
  )
}

// ── Inner Page (requires auth) ────────────────────────────────────────

function KgReviewContent() {
  const [items, setItems] = useState<ReadonlyArray<ReviewItem>>([])
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [statusFilter, setStatusFilter] = useState('pending')

  // Aggregate counts (fetched separately for all statuses)
  const [stats, setStats] = useState({ pending: 0, approved: 0, rejected: 0 })

  const loadQueue = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const queryParams = statusFilter === 'all' ? 'pending' : statusFilter
      const data = await getKgReviewQueue(queryParams, page, PAGE_SIZE)
      setItems(data.items)
      setTotal(data.total)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : '加载失败'
      setError(message)
    } finally {
      setLoading(false)
    }
  }, [statusFilter, page])

  const loadStats = useCallback(async () => {
    try {
      const [pendingData, approvedData, rejectedData] = await Promise.all([
        getKgReviewQueue('pending', 1, 1),
        getKgReviewQueue('approved', 1, 1),
        getKgReviewQueue('rejected', 1, 1),
      ])
      setStats({
        pending: pendingData.total,
        approved: approvedData.total,
        rejected: rejectedData.total,
      })
    } catch {
      // Stats are secondary — don't block the page
    }
  }, [])

  useEffect(() => {
    loadQueue()
    loadStats()
  }, [loadQueue, loadStats])

  const handleSelect = useCallback((id: string, selected: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (selected) {
        next.add(id)
      } else {
        next.delete(id)
      }
      return next
    })
  }, [])

  const handleSelectAll = useCallback((selected: boolean) => {
    setSelectedIds((_prev) => {
      if (selected) {
        return new Set(items.map((item) => item.id))
      }
      return new Set()
    })
  }, [items])

  const handleBatchAction = useCallback(
    async (action: 'approve' | 'reject', ids: ReadonlyArray<string>) => {
      try {
        await batchKgAction(action, ids)
        setSelectedIds(new Set())
        await loadQueue()
        await loadStats()
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : '操作失败'
        alert(message)
      }
    },
    [loadQueue, loadStats],
  )

  const handleItemAction = useCallback(
    async (_id: string, action: 'approve' | 'reject') => {
      try {
        await batchKgAction(action, [_id])
        await loadQueue()
        await loadStats()
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : '操作失败'
        alert(message)
      }
    },
    [loadQueue, loadStats],
  )

  const handleFilterChange = useCallback((value: string) => {
    setStatusFilter(value)
    setPage(1)
  }, [])

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
        <PageHeader
          title="知识图谱审核"
          filterValue={statusFilter}
          onFilterChange={handleFilterChange}
          onRefresh={loadQueue}
        />

        {error && (
          <div className="mb-4 px-4 py-3 rounded-lg bg-red-900/30 border border-red-700 text-red-300 text-sm">
            {error}
          </div>
        )}

        <ReviewQueueTable
          items={items}
          selectedIds={selectedIds}
          onSelect={handleSelect}
          onSelectAll={handleSelectAll}
          onBatchAction={handleBatchAction}
          onItemAction={handleItemAction}
          loading={loading}
          pagination={
            total > 0
              ? { page, total, pageSize: PAGE_SIZE, onChange: setPage }
              : undefined
          }
        />

        <div className="mt-4">
          <StatusBar {...stats} />
        </div>
      </div>
  )
}

// ── Page Export ────────────────────────────────────────────────────────

export default function KgReviewPage() {
  return <KgReviewContent />
}
