/**
 * Conflict Resolution Page — ReviewQueueTable + slide-over detail panel.
 *
 * Route: /review/conflicts
 * Spec: NFM-848 §3.8, NFM-1006
 *
 * Auth is handled by review/layout.tsx (BlogAuthGuard wrapper).
 */

'use client'

import { useState, useEffect, useCallback } from 'react'
import {
  ReviewQueueTable,
  type ReviewItem,
} from '@/components/review/ReviewQueueTable'
import { ConflictDetailPanel } from '@/components/review/ConflictDetailPanel'
import {
  fetchConflicts,
  resolveConflict,
  type ConflictItem,
  type ConflictResolutionAction,
} from '@/lib/kg-review-api'

// ── Constants ──────────────────────────────────────────────────────────

const PAGE_SIZE = 20

// ── Helpers ────────────────────────────────────────────────────────────

function mapToReviewItem(conflict: ConflictItem): ReviewItem {
  return {
    id: conflict.id,
    title: `${conflict.entityName} — ${conflict.property}`,
    type: conflict.property,
    source: conflict.sourceA.sourceTitle,
    confidence: Math.max(
      conflict.sourceA.confidence,
      conflict.sourceB.confidence,
    ),
    status: 'pending',
    createdAt: '',
  }
}

// ── Inner Page ─────────────────────────────────────────────────────────

function ConflictsContent() {
  const [conflicts, setConflicts] = useState<ReadonlyArray<ConflictItem>>([])
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [activeConflictId, setActiveConflictId] = useState<string | null>(null)
  const [resolvingId, setResolvingId] = useState<string | null>(null)

  const reviewItems: ReadonlyArray<ReviewItem> = conflicts.map(mapToReviewItem)

  const activeConflict: ConflictItem | null =
    conflicts.find((c) => c.id === activeConflictId) ?? null

  // ── Data fetching ───────────────────────────────────────────────────

  const loadConflicts = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchConflicts('pending', page, PAGE_SIZE)
      setConflicts(data.items)
      setTotal(data.total)
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : '加载冲突队列失败'
      setError(message)
    } finally {
      setLoading(false)
    }
  }, [page])

  useEffect(() => {
    void loadConflicts()
  }, [loadConflicts])

  // ── Selection ───────────────────────────────────────────────────────

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
        return new Set(conflicts.map((c) => c.id))
      }
      return new Set()
    })
  }, [conflicts])

  // ── Row action → open detail panel ─────────────────────────────────

  const handleItemAction = useCallback((_id: string, _action: 'approve' | 'reject') => {
    setActiveConflictId(_id)
  }, [])

  // ── Batch action → bulk skip ────────────────────────────────────────

  const handleBatchAction = useCallback(
    async (action: 'approve' | 'reject', ids: ReadonlyArray<string>) => {
      const bulkAction: ConflictResolutionAction =
        action === 'approve' ? 'keep_a' : 'skip'
      try {
        await Promise.all(
          ids.map((id) => resolveConflict(id, bulkAction)),
        )
        setSelectedIds(new Set())
        await loadConflicts()
      } catch (err: unknown) {
        const message =
          err instanceof Error ? err.message : '批量操作失败'
        setError(message)
      }
    },
    [loadConflicts],
  )

  // ── Detail panel resolve ───────────────────────────────────────────

  const handleResolve = useCallback(
    async (conflictId: string, action: ConflictResolutionAction) => {
      setResolvingId(conflictId)
      try {
        await resolveConflict(conflictId, action)
        setActiveConflictId(null)
        await loadConflicts()
      } catch (err: unknown) {
        const message =
          err instanceof Error ? err.message : '冲突解决失败'
        setError(message)
      } finally {
        setResolvingId(null)
      }
    },
    [loadConflicts],
  )

  const handleClosePanel = useCallback(() => {
    setActiveConflictId(null)
  }, [])

  return (
    <main className="max-w-6xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-100">冲突解决</h1>
        <button
          type="button"
          onClick={() => void loadConflicts()}
          className="px-3 py-1.5 rounded-lg bg-gray-700 border border-gray-600 text-gray-200 text-sm hover:bg-gray-600 transition-colors"
          aria-label="刷新"
        >
          刷新
        </button>
      </div>

      {error && (
        <div className="mb-4 px-4 py-3 rounded-lg bg-red-900/30 border border-red-700 text-red-300 text-sm">
          {error}
        </div>
      )}

      <ReviewQueueTable
        items={reviewItems}
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

      <ConflictDetailPanel
        conflict={activeConflict}
        loading={resolvingId === activeConflictId}
        onResolve={handleResolve}
        onClose={handleClosePanel}
      />
    </main>
  )
}

// ── Page Export ────────────────────────────────────────────────────────

export default function ConflictsReviewPage() {
  return <ConflictsContent />
}
