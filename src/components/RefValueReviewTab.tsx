'use client'

import { useState, useEffect, useCallback } from 'react'
import type { ReferenceValue } from '@/lib/types'
import EditSourceModal from './EditSourceModal'
import Pagination from './Pagination'

// ─── Constants ──────────────────────────────────────────────────────────────────

const SYSTEM_OPTIONS = [
  'U', 'Mo', 'Zr', 'Nb', 'U-Mo', 'U-Zr', 'U-Nb', 'U-Pu', 'U-Pu-Zr', 'Pu', 'Fe', 'Cr', 'SiC',
]

const CONFIDENCE_OPTIONS = ['high', 'medium', 'low'] as const

const ISSUE_LABELS: Record<string, string> = {
  missing_doi: '缺少 DOI',
  vague_source: '来源模糊',
  interpolated: '插值估算',
  no_uncertainty: '无不确定度',
}

const ISSUE_COLORS: Record<string, string> = {
  missing_doi: 'bg-orange-900/40 text-orange-300 border-orange-700',
  vague_source: 'bg-yellow-900/40 text-yellow-300 border-yellow-700',
  interpolated: 'bg-purple-900/40 text-purple-300 border-purple-700',
  no_uncertainty: 'bg-gray-700 text-gray-400 border-gray-600',
}

const CONFIDENCE_BADGE: Record<string, string> = {
  high: 'bg-green-900/40 text-green-300 border-green-700',
  medium: 'bg-yellow-900/40 text-yellow-300 border-yellow-700',
  low: 'bg-red-900/40 text-red-300 border-red-700',
}

// ─── Interfaces ─────────────────────────────────────────────────────────────────

interface RefValueReviewTabProps {
  sessionToken: string
}

interface RefValueListResponse {
  items: ReferenceValue[]
  total: number
  page: number
  totalPages: number
}

// ─── Sub-components ──────────────────────────────────────────────────────────────

function ConfidenceBadge({ level }: { level: string | null }) {
  if (!level) return null
  const label = level === 'high' ? '高' : level === 'medium' ? '中' : '低'
  return (
    <span className={`text-xs px-2 py-0.5 rounded border ${CONFIDENCE_BADGE[level] || ''}`}>
      {label}
    </span>
  )
}

function DetectIssues(item: ReferenceValue): string[] {
  const issues: string[] = []
  if (!item.source_doi) issues.push('missing_doi')
  if (!item.source || item.source.trim().length < 3) issues.push('vague_source')
  if (!item.uncertainty) issues.push('no_uncertainty')
  if (item.method && (item.method.toLowerCase().includes('interp') || item.method.toLowerCase().includes('extrap'))) {
    issues.push('interpolated')
  }
  return issues
}

function RefValueCard({
  item,
  selected,
  onToggleSelect,
  onApprove,
  onReject,
  onEditSource,
  actionInProgress,
}: {
  item: ReferenceValue
  selected: boolean
  onToggleSelect: () => void
  onApprove: (id: string) => void
  onReject: (id: string) => void
  onEditSource: (item: ReferenceValue) => void
  actionInProgress: boolean
}) {
  const issues = DetectIssues(item)
  const isLoading = actionInProgress

  return (
    <div className={`bg-gray-800 border rounded-xl p-4 transition ${selected ? 'border-blue-500' : 'border-gray-700'}`}>
      <div className="flex items-start gap-3">
        {/* Checkbox */}
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelect}
          className="mt-1 w-4 h-4 rounded bg-gray-700 border-gray-600 text-blue-500 focus:ring-blue-500 focus:ring-offset-0 cursor-pointer"
        />

        {/* Content */}
        <div className="flex-1 min-w-0">
          {/* Title line */}
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className="text-sm font-medium text-white">
              {item.element_system}
            </span>
            {item.phase && (
              <span className="text-xs px-1.5 py-0.5 bg-gray-700 rounded text-gray-400">
                {item.phase}
              </span>
            )}
            <span className="text-gray-500">·</span>
            <span className="text-sm text-gray-300">
              {item.property} = <span className="text-white font-medium">{item.value}</span> {item.unit}
            </span>
            {item.temperature != null && (
              <span className="text-xs text-gray-500">
                @{item.temperature} K
              </span>
            )}
            <ConfidenceBadge level={item.confidence} />
          </div>

          {/* Source */}
          <div className="text-xs text-gray-400 mt-1">
            来源：
            <span className="text-gray-300">{item.source || '未提供'}</span>
            {item.source_doi && (
              <span className="ml-1 text-blue-400">DOI: {item.source_doi}</span>
            )}
            {item.method && (
              <span className="ml-2">方法：{item.method}</span>
            )}
          </div>

          {/* Issue tags */}
          {issues.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {issues.map(key => (
                <span
                  key={key}
                  className={`text-xs px-2 py-0.5 rounded border ${ISSUE_COLORS[key] || ISSUE_COLORS.vague_source}`}
                >
                  {ISSUE_LABELS[key] || key}
                </span>
              ))}
            </div>
          )}

          {/* Action buttons */}
          <div className="flex gap-2 mt-3">
            <button
              onClick={() => onApprove(item.id)}
              disabled={isLoading}
              className="px-3 py-1.5 text-xs rounded-lg bg-green-700 hover:bg-green-600 text-white transition disabled:opacity-50 disabled:cursor-not-allowed"
            >
              确认采纳
            </button>
            <button
              onClick={() => onReject(item.id)}
              disabled={isLoading}
              className="px-3 py-1.5 text-xs rounded-lg bg-red-800 hover:bg-red-700 text-white transition disabled:opacity-50 disabled:cursor-not-allowed"
            >
              拒绝
            </button>
            <button
              onClick={() => onEditSource(item)}
              className="px-3 py-1.5 text-xs rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-200 transition"
            >
              补充来源
            </button>
            <button
              onClick={() => onEditSource(item)}
              className="px-3 py-1.5 text-xs rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-200 transition"
            >
              修正值
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function ConfirmDialog({
  title,
  message,
  confirmLabel,
  onConfirm,
  onCancel,
  inputPlaceholder,
  extraContent,
}: {
  title: string
  message: string
  confirmLabel: string
  onConfirm: (input?: string) => void
  onCancel: () => void
  inputPlaceholder?: string
  extraContent?: React.ReactNode
}) {
  const [input, setInput] = useState('')
  const needsInput = !!inputPlaceholder

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-gray-800 border border-gray-700 rounded-xl w-full max-w-md mx-4 p-6">
        <h3 className="text-lg font-semibold text-white mb-2">{title}</h3>
        <p className="text-sm text-gray-400 mb-4">{message}</p>

        {extraContent}

        {needsInput && (
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            rows={3}
            className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-gray-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none mb-4"
            placeholder={inputPlaceholder}
          />
        )}

        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm rounded-lg bg-gray-700 text-gray-300 hover:bg-gray-600 transition"
          >
            取消
          </button>
          <button
            onClick={() => {
              if (needsInput && !input.trim()) return
              onConfirm(needsInput ? input.trim() : undefined)
            }}
            disabled={needsInput && !input.trim()}
            className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-500 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Main Component ─────────────────────────────────────────────────────────────

export default function RefValueReviewTab({ sessionToken }: RefValueReviewTabProps) {
  // List state
  const [items, setItems] = useState<ReferenceValue[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Filters
  const [systemFilter, setSystemFilter] = useState('')
  const [confidenceFilter, setConfidenceFilter] = useState('')

  // Selection
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [allSelected, setAllSelected] = useState(false)

  // Action state
  const [actionInProgress, setActionInProgress] = useState<string | null>(null)
  const [batchActionInProgress, setBatchActionInProgress] = useState(false)

  // Dialogs
  const [approveTarget, setApproveTarget] = useState<ReferenceValue | null>(null)
  const [rejectTarget, setRejectTarget] = useState<ReferenceValue | null>(null)
  const [editModalItem, setEditModalItem] = useState<ReferenceValue | null>(null)

  const PAGE_SIZE = 20

  const fetchItems = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        needs_review: 'true',
        page: String(page),
        limit: String(PAGE_SIZE),
      })
      if (systemFilter) params.set('element_system', systemFilter)
      if (confidenceFilter) params.set('confidence', confidenceFilter)

      const res = await fetch(`/api/admin/reference-values?${params}`, {
        headers: { Authorization: `Bearer ${sessionToken}` },
      })
      const data: RefValueListResponse = await res.json()
      if (!res.ok) throw new Error((data as any).error || '加载失败')

      setItems(data.items || [])
      setTotal(data.total || 0)
      setTotalPages(data.totalPages || 1)
      setSelectedIds(new Set())
      setAllSelected(false)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [sessionToken, page, systemFilter, confidenceFilter])

  useEffect(() => {
    fetchItems()
  }, [fetchItems])

  // ── Selection ──────────────────────────────────────────────────────────────

  function toggleSelect(id: string) {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      setAllSelected(next.size === items.length && items.length > 0)
      return next
    })
  }

  function toggleSelectAll() {
    if (allSelected) {
      setSelectedIds(new Set())
      setAllSelected(false)
    } else {
      setSelectedIds(new Set(items.map(i => i.id)))
      setAllSelected(true)
    }
  }

  // ── Single actions ─────────────────────────────────────────────────────────

  async function handleApprove(id: string, confidenceUpgrade?: string) {
    setActionInProgress(id)
    setError(null)
    try {
      const body: Record<string, unknown> = {}
      if (confidenceUpgrade) body.confidence = confidenceUpgrade

      const res = await fetch(`/api/admin/reference-values/${id}/approve`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${sessionToken}`,
        },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error || '操作失败')
      }
      fetchItems()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setActionInProgress(null)
    }
  }

  async function handleReject(id: string, reason: string) {
    setActionInProgress(id)
    setError(null)
    try {
      const res = await fetch(`/api/admin/reference-values/${id}/reject`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${sessionToken}`,
        },
        body: JSON.stringify({ reason }),
      })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error || '操作失败')
      }
      fetchItems()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setActionInProgress(null)
    }
  }

  // ── Batch actions ─────────────────────────────────────────────────────────

  async function handleBatchApprove() {
    if (selectedIds.size === 0) return
    setBatchActionInProgress(true)
    setError(null)
    try {
      const res = await fetch('/api/admin/reference-values/batch-approve', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${sessionToken}`,
        },
        body: JSON.stringify({ ids: Array.from(selectedIds) }),
      })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error || '批量操作失败')
      }
      fetchItems()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBatchActionInProgress(false)
    }
  }

  async function handleBatchReject() {
    if (selectedIds.size === 0) return
    setBatchActionInProgress(true)
    setError(null)
    try {
      const res = await fetch('/api/admin/reference-values/batch-reject', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${sessionToken}`,
        },
        body: JSON.stringify({ ids: Array.from(selectedIds), reason: '批量拒绝' }),
      })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error || '批量操作失败')
      }
      fetchItems()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBatchActionInProgress(false)
    }
  }

  // ── Filter change resets page ──────────────────────────────────────────────

  useEffect(() => {
    setPage(1)
  }, [systemFilter, confidenceFilter])

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div>
      {error && (
        <div className="mb-4 px-4 py-3 bg-red-900/40 border border-red-700 rounded-lg text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <select
          value={systemFilter}
          onChange={e => setSystemFilter(e.target.value)}
          className="px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">全部体系</option>
          {SYSTEM_OPTIONS.map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>

        <select
          value={confidenceFilter}
          onChange={e => setConfidenceFilter(e.target.value)}
          className="px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">全部置信度</option>
          {CONFIDENCE_OPTIONS.map(c => (
            <option key={c} value={c}>
              {c === 'high' ? '高' : c === 'medium' ? '中' : '低'}
            </option>
          ))}
        </select>

        <button
          onClick={fetchItems}
          className="text-xs text-gray-400 hover:text-white transition px-3 py-2 border border-gray-700 rounded-lg"
        >
          刷新
        </button>

        <span className="text-gray-400 text-sm ml-auto">
          共 <span className="text-yellow-400 font-medium">{total}</span> 条待审核
        </span>
      </div>

      {/* Batch action bar */}
      {selectedIds.size > 0 && (
        <div className="mb-4 flex items-center gap-3 px-4 py-2 bg-gray-800 border border-blue-800/40 rounded-lg">
          <span className="text-sm text-blue-300">已选 {selectedIds.size} 条</span>
          <button
            onClick={handleBatchApprove}
            disabled={batchActionInProgress}
            className="px-3 py-1.5 text-xs rounded-lg bg-green-700 hover:bg-green-600 text-white transition disabled:opacity-50"
          >
            {batchActionInProgress ? '处理中...' : '批量确认'}
          </button>
          <button
            onClick={handleBatchReject}
            disabled={batchActionInProgress}
            className="px-3 py-1.5 text-xs rounded-lg bg-red-800 hover:bg-red-700 text-white transition disabled:opacity-50"
          >
            {batchActionInProgress ? '处理中...' : '批量拒绝'}
          </button>
          <button
            onClick={() => { setSelectedIds(new Set()); setAllSelected(false) }}
            className="text-xs text-gray-400 hover:text-gray-200 ml-auto"
          >
            取消选择
          </button>
        </div>
      )}

      {/* Select all */}
      {!loading && items.length > 0 && (
        <div className="flex items-center gap-2 mb-3">
          <input
            type="checkbox"
            checked={allSelected}
            onChange={toggleSelectAll}
            className="w-4 h-4 rounded bg-gray-700 border-gray-600 text-blue-500 focus:ring-blue-500 focus:ring-offset-0 cursor-pointer"
          />
          <span className="text-xs text-gray-400">全选本页</span>
        </div>
      )}

      {/* List */}
      {loading ? (
        <div className="text-gray-500 py-8 text-center">加载中...</div>
      ) : items.length === 0 ? (
        <div className="py-16 text-center text-gray-500">
          <div className="text-4xl mb-3">✅</div>
          <div>没有待审核的参考值</div>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map(item => (
            <RefValueCard
              key={item.id}
              item={item}
              selected={selectedIds.has(item.id)}
              onToggleSelect={() => toggleSelect(item.id)}
              onApprove={id => setApproveTarget(items.find(i => i.id === id) || null)}
              onReject={id => setRejectTarget(items.find(i => i.id === id) || null)}
              onEditSource={item => setEditModalItem(item)}
              actionInProgress={actionInProgress === item.id}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      <Pagination
        currentPage={page}
        totalPages={totalPages}
        onPageChange={setPage}
      />

      {/* Confirm dialogs */}
      {approveTarget && (
        <ConfirmDialog
          title="确认采纳"
          message={`确认采纳该参考值？${approveTarget.element_system} | ${approveTarget.property} = ${approveTarget.value} ${approveTarget.unit}`}
          confirmLabel="确认采纳"
          inputPlaceholder={undefined}
          onCancel={() => setApproveTarget(null)}
          onConfirm={() => {
              const sel = document.getElementById('approve-confidence') as HTMLSelectElement | null
              handleApprove(approveTarget!.id, sel?.value || undefined)
            }}
          extraContent={
            <div className="mb-4">
              <label className="block text-sm text-gray-300 mb-2">可选：提升置信度</label>
              <select
                id="approve-confidence"
                className="px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
                defaultValue=""
              >
                <option value="">不更改</option>
                {CONFIDENCE_OPTIONS.map(c => (
                  <option key={c} value={c}>
                    {c === 'high' ? '高' : c === 'medium' ? '中' : '低'}
                  </option>
                ))}
              </select>
            </div>
          }
        />
      )}

      {rejectTarget && (
        <ConfirmDialog
          title="拒绝参考值"
          message="请输入拒绝原因："
          confirmLabel="确认拒绝"
          inputPlaceholder="拒绝原因（必填）"
          onCancel={() => setRejectTarget(null)}
          onConfirm={(reason) => {
            if (reason) handleReject(rejectTarget.id, reason)
            setRejectTarget(null)
          }}
        />
      )}

      {/* Edit Source Modal */}
      {editModalItem && (
        <EditSourceModal
          item={editModalItem}
          sessionToken={sessionToken}
          open={true}
          onClose={() => setEditModalItem(null)}
          onSaved={fetchItems}
        />
      )}
    </div>
  )
}
