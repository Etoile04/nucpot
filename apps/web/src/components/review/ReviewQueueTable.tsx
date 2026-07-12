/**
 * ReviewQueueTable — review queue with selection, batch actions, and pagination.
 *
 * Spec: NFM-848 §2.3
 */

import { useState, useCallback } from 'react'
import { ConfidenceBadge } from '@/components/shared/ConfidenceBadge'

// ── Types ──────────────────────────────────────────────────────────────

interface ReviewItem {
  readonly id: string
  readonly title: string
  readonly type: string
  readonly source: string
  readonly confidence: number
  readonly status: 'pending' | 'approved' | 'rejected'
  readonly createdAt: string
}

interface PaginationConfig {
  readonly page: number
  readonly total: number
  readonly pageSize: number
  readonly onChange: (page: number) => void
}

interface ReviewQueueTableProps {
  readonly items: ReadonlyArray<ReviewItem>
  readonly selectedIds: ReadonlySet<string>
  readonly onSelect: (id: string, selected: boolean) => void
  readonly onSelectAll: (selected: boolean) => void
  readonly onBatchAction: (action: 'approve' | 'reject', ids: ReadonlyArray<string>) => void
  readonly onItemAction: (id: string, action: 'approve' | 'reject') => void
  readonly loading?: boolean
  readonly pagination?: PaginationConfig
}

// ── Helpers ────────────────────────────────────────────────────────────

const STATUS_CONFIG: Record<ReviewItem['status'], { label: string; className: string }> = {
  pending: { label: '待审', className: 'text-amber-400' },
  approved: { label: '已通过', className: 'text-emerald-400' },
  rejected: { label: '已拒绝', className: 'text-red-400' },
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function totalPages(pageSize: number, total: number): number {
  return Math.ceil(total / pageSize)
}

// ── Component ──────────────────────────────────────────────────────────

export { type ReviewItem, type PaginationConfig, type ReviewQueueTableProps }

export function ReviewQueueTable({
  items,
  selectedIds,
  onSelect,
  onSelectAll,
  onBatchAction,
  onItemAction,
  loading = false,
  pagination,
}: ReviewQueueTableProps) {
  const [confirmAction, setConfirmAction] = useState<'approve' | 'reject' | null>(null)

  const allSelected = items.length > 0 && items.every(
    (item) => selectedIds.has(item.id),
  )

  const handleSelectAllChange = useCallback(() => {
    onSelectAll(!allSelected)
  }, [onSelectAll, allSelected])

  const handleBatchApprove = useCallback(() => {
    setConfirmAction('approve')
  }, [])

  const handleBatchReject = useCallback(() => {
    setConfirmAction('reject')
  }, [])

  const handleConfirm = useCallback(() => {
    if (confirmAction === null) return
    const ids = Array.from(selectedIds)
    onBatchAction(confirmAction, ids)
    setConfirmAction(null)
  }, [confirmAction, selectedIds, onBatchAction])

  const handleCancelConfirm = useCallback(() => {
    setConfirmAction(null)
  }, [])

  return (
    <div className="relative">
      {/* Table container */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-700">
            {/* Header */}
            <thead className="bg-gray-900">
              <tr>
                <th scope="col" className="px-4 py-3 text-left">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={handleSelectAllChange}
                    aria-label="选择全部"
                    className="rounded border-gray-600 bg-gray-700 text-emerald-400 focus:ring-emerald-500"
                  />
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs uppercase tracking-wider text-gray-400">
                  标题
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs uppercase tracking-wider text-gray-400">
                  类型
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs uppercase tracking-wider text-gray-400">
                  来源
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs uppercase tracking-wider text-gray-400">
                  置信度
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs uppercase tracking-wider text-gray-400">
                  状态
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs uppercase tracking-wider text-gray-400">
                  创建时间
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs uppercase tracking-wider text-gray-400">
                  操作
                </th>
              </tr>
            </thead>

            {/* Body */}
            <tbody className="divide-y divide-gray-700/50">
              {items.map((item) => {
                const isSelected = selectedIds.has(item.id)
                const statusCfg = STATUS_CONFIG[item.status]

                return (
                  <tr
                    key={item.id}
                    className={[
                      'border-b border-gray-700/50 hover:bg-gray-700/40 transition-colors',
                      isSelected ? 'bg-blue-900/20' : '',
                    ].filter(Boolean).join(' ')}
                  >
                    <td className="px-4 py-3" scope="row">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => onSelect(item.id, !isSelected)}
                        aria-label={`选择 ${item.title}`}
                        className="rounded border-gray-600 bg-gray-700 text-emerald-400 focus:ring-emerald-500"
                      />
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-200 font-medium">
                      {item.title}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-400">
                      {item.type}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-400 truncate max-w-[150px]">
                      {item.source}
                    </td>
                    <td className="px-4 py-3">
                      <ConfidenceBadge value={item.confidence} size="sm" />
                    </td>
                    <td className="px-4 py-3">
                      <span className={`text-sm ${statusCfg.className}`}>
                        {statusCfg.label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-400">
                      {formatDate(item.createdAt)}
                    </td>
                    <td className="px-4 py-3 text-right space-x-2">
                      <button
                        type="button"
                        onClick={() => onItemAction(item.id, 'approve')}
                        disabled={item.status !== 'pending'}
                        className="inline-flex items-center rounded px-2 py-1 text-xs font-medium bg-emerald-600 hover:bg-emerald-700 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        aria-label={`通过 ${item.title}`}
                      >
                        通过
                      </button>
                      <button
                        type="button"
                        onClick={() => onItemAction(item.id, 'reject')}
                        disabled={item.status !== 'pending'}
                        className="inline-flex items-center rounded px-2 py-1 text-xs font-medium bg-red-600 hover:bg-red-700 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        aria-label={`拒绝 ${item.title}`}
                      >
                        拒绝
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* Empty state */}
        {items.length === 0 && !loading && (
          <div className="px-4 py-12 text-center text-gray-500">
            <p className="text-lg">暂无待审项目</p>
            <p className="text-sm mt-1">新的提取数据将显示在此处</p>
          </div>
        )}

        {/* Loading overlay */}
        {loading && (
          <div className="absolute inset-0 bg-gray-800/60 flex items-center justify-center" role="status" aria-label="加载中">
            <svg
              className="animate-spin h-8 w-8 text-emerald-400"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
              />
            </svg>
          </div>
        )}
      </div>

      {/* Batch action bar */}
      {selectedIds.size > 0 && (
        <div className="px-4 py-3 bg-gray-900 border-t border-gray-700 flex items-center justify-between animate-slide-down">
          <span className="text-sm text-gray-300">
            已选择 <strong className="text-white">{selectedIds.size}</strong> 项
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleBatchApprove}
              className="inline-flex items-center rounded px-3 py-1.5 text-sm font-medium bg-emerald-600 hover:bg-emerald-700 text-white transition-colors"
              aria-label={`批量通过 ${selectedIds.size} 项`}
            >
              批量通过
            </button>
            <button
              type="button"
              onClick={handleBatchReject}
              className="inline-flex items-center rounded px-3 py-1.5 text-sm font-medium bg-red-600 hover:bg-red-700 text-white transition-colors"
              aria-label={`批量拒绝 ${selectedIds.size} 项`}
            >
              批量拒绝
            </button>
          </div>
        </div>
      )}

      {/* Pagination footer */}
      {pagination && pagination.total > 0 && (
        <div className="px-4 py-3 bg-gray-900 border-t border-gray-700 flex items-center justify-between">
          <span className="text-sm text-gray-400">
            共 {pagination.total} 条
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              disabled={pagination.page <= 1}
              onClick={() => pagination.onChange(pagination.page - 1)}
              className="px-3 py-1 text-sm rounded border border-gray-600 text-gray-300 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              aria-label="上一页"
            >
              ‹
            </button>
            {Array.from({ length: totalPages(pagination.pageSize, pagination.total) }, (_, i) => i + 1).map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => pagination.onChange(p)}
                className={[
                  'px-3 py-1 text-sm rounded border transition-colors',
                  p === pagination.page
                    ? 'border-emerald-500 bg-emerald-600/20 text-emerald-400'
                    : 'border-gray-600 text-gray-400 hover:bg-gray-700',
                ].join(' ')}
                aria-label={`第 ${p} 页`}
                aria-current={p === pagination.page ? 'page' : undefined}
              >
                {p}
              </button>
            ))}
            <button
              type="button"
              disabled={pagination.page >= totalPages(pagination.pageSize, pagination.total)}
              onClick={() => pagination.onChange(pagination.page + 1)}
              className="px-3 py-1 text-sm rounded border border-gray-600 text-gray-300 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              aria-label="下一页"
            >
              ›
            </button>
          </div>
        </div>
      )}

      {/* Confirmation modal */}
      {confirmAction !== null && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          role="dialog"
          aria-modal="true"
          aria-label="确认操作"
        >
          <div className="bg-gray-800 rounded-lg border border-gray-700 p-6 max-w-sm w-full mx-4 shadow-xl">
            <h3 className="text-lg font-medium text-gray-100 mb-2">
              {confirmAction === 'approve' ? '批量通过' : '批量拒绝'}
            </h3>
            <p className="text-sm text-gray-400 mb-6">
              确定{confirmAction === 'approve' ? '通过' : '拒绝'}选中的 {selectedIds.size} 项吗？此操作不可撤销。
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={handleCancelConfirm}
                className="px-4 py-2 text-sm rounded border border-gray-600 text-gray-300 hover:bg-gray-700 transition-colors"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleConfirm}
                className={[
                  'px-4 py-2 text-sm rounded font-medium text-white transition-colors',
                  confirmAction === 'approve'
                    ? 'bg-emerald-600 hover:bg-emerald-700'
                    : 'bg-red-600 hover:bg-red-700',
                ].join(' ')}
              >
                确认{confirmAction === 'approve' ? '通过' : '拒绝'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
