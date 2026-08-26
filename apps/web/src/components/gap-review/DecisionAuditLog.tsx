/**
 * DecisionAuditLog — read-only table of immutable decision history.
 *
 * Displays audit entries with filters (reviewer, date range, decision type,
 * entity name) and cursor-based pagination. All filters and cursor
 * position sync to URL params for shareability.
 * No edit/delete actions — this is an immutable ledger.
 *
 * Spec: NFM-3708, NFM-3759 cursor pagination, UX ref NFM-3682 §3.4/§7
 */

'use client'

import { useState, useEffect, useCallback } from 'react'
import { useAuditLogFilters } from './useAuditLogFilters'
import { getAuditLog } from '@/lib/reference-gaps/api'
import type { AuditEntry, AuditLogResponse, DecisionKind } from '@/lib/reference-gaps/types'
import { ConfidenceBadge } from '@/components/shared/ConfidenceBadge'

// ── Decision display config ──────────────────────────────────────────

const DECISION_STYLE: Record<DecisionKind, { label: string; className: string }> = {
  accepted: { label: '已接受', className: 'text-emerald-400 bg-emerald-900/40' },
  rejected: { label: '已拒绝', className: 'text-red-400 bg-red-900/40' },
  deferred: { label: '已延期', className: 'text-amber-400 bg-amber-900/40' },
}

// ── Helpers ──────────────────────────────────────────────────────────

function formatDateTime(iso: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(d.getTime())
    ? '—'
    : d.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
}

// ── Sub-components ───────────────────────────────────────────────────

function FilterBar({
  filters,
  decisionOptions,
  onFilterChange,
  onReset,
}: {
  readonly filters: Record<string, string | undefined>
  readonly decisionOptions: ReadonlyArray<{ value: string; label: string }>
  readonly onFilterChange: (update: Partial<Record<string, string | undefined>>) => void
  readonly onReset: () => void
}) {
  const hasActiveFilter = Object.values(filters).some((v) => v !== undefined && v !== '')

  return (
    <div className="mb-4 flex flex-wrap items-end gap-3">
      {/* Entity name */}
      <label className="flex flex-col gap-1">
        <span className="text-xs text-gray-400">实体名称</span>
        <input
          type="text"
          value={filters.entity_name ?? ''}
          onChange={(e) => onFilterChange({ entity_name: e.target.value || undefined })}
          placeholder="Uranium Dioxide"
          className="rounded-md border border-gray-600 bg-gray-700 px-3 py-1.5 text-sm text-gray-200 placeholder-gray-500 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
      </label>

      {/* Reviewer ID */}
      <label className="flex flex-col gap-1">
        <span className="text-xs text-gray-400">审核员</span>
        <input
          type="text"
          value={filters.reviewer_id ?? ''}
          onChange={(e) => onFilterChange({ reviewer_id: e.target.value || undefined })}
          placeholder="reviewer@example.com"
          className="rounded-md border border-gray-600 bg-gray-700 px-3 py-1.5 text-sm text-gray-200 placeholder-gray-500 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
      </label>

      {/* Decision type */}
      <label className="flex flex-col gap-1">
        <span className="text-xs text-gray-400">决策类型</span>
        <select
          value={filters.decision ?? ''}
          onChange={(e) => onFilterChange({ decision: (e.target.value || undefined) as DecisionKind | undefined })}
          className="rounded-md border border-gray-600 bg-gray-700 px-3 py-1.5 text-sm text-gray-200 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        >
          <option value="">全部</option>
          {decisionOptions.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>

      {/* Date from */}
      <label className="flex flex-col gap-1">
        <span className="text-xs text-gray-400">开始日期</span>
        <input
          type="date"
          value={filters.date_from ?? ''}
          onChange={(e) => onFilterChange({ date_from: e.target.value || undefined })}
          className="rounded-md border border-gray-600 bg-gray-700 px-3 py-1.5 text-sm text-gray-200 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
      </label>

      {/* Date to */}
      <label className="flex flex-col gap-1">
        <span className="text-xs text-gray-400">结束日期</span>
        <input
          type="date"
          value={filters.date_to ?? ''}
          onChange={(e) => onFilterChange({ date_to: e.target.value || undefined })}
          className="rounded-md border border-gray-600 bg-gray-700 px-3 py-1.5 text-sm text-gray-200 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
      </label>

      {/* Reset */}
      {hasActiveFilter && (
        <button
          type="button"
          onClick={onReset}
          className="rounded-md border border-gray-600 bg-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-600 transition-colors"
          aria-label="清除筛选"
        >
          清除筛选
        </button>
      )}
    </div>
  )
}

function AuditTable({
  entries,
  hasNext,
  hasPrev,
  onPrev,
  onNext,
}: {
  readonly entries: ReadonlyArray<AuditEntry>
  readonly hasNext: boolean
  readonly hasPrev: boolean
  readonly onPrev: () => void
  readonly onNext: () => void
}) {

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
      {/* Desktop table */}
      <div className="hidden md:block overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-700">
          <thead className="bg-gray-900">
            <tr>
              <th scope="col" className="px-4 py-3 text-left text-xs uppercase tracking-wider text-gray-400">时间</th>
              <th scope="col" className="px-4 py-3 text-left text-xs uppercase tracking-wider text-gray-400">审核员</th>
              <th scope="col" className="px-4 py-3 text-left text-xs uppercase tracking-wider text-gray-400">实体</th>
              <th scope="col" className="px-4 py-3 text-left text-xs uppercase tracking-wider text-gray-400">决策</th>
              <th scope="col" className="px-4 py-3 text-left text-xs uppercase tracking-wider text-gray-400">置信度</th>
              <th scope="col" className="px-4 py-3 text-left text-xs uppercase tracking-wider text-gray-400">来源文档</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-700/50">
            {entries.map((entry) => {
              const style = DECISION_STYLE[entry.decision] ?? { label: entry.decision, className: 'text-gray-400 bg-gray-700' }
              return (
                <tr
                  key={entry.id}
                  className="border-b border-gray-700/50 hover:bg-gray-700/40 transition-colors"
                >
                  <td className="px-4 py-3 text-sm text-gray-300 whitespace-nowrap">
                    {formatDateTime(entry.decided_at)}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-200 font-medium">
                    {entry.reviewer_name}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-200">
                    {entry.entity_name}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${style.className}`}>
                      {style.label}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <ConfidenceBadge value={entry.confidence} />
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-400 truncate max-w-[200px]" title={entry.source_document}>
                    {entry.source_document || '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile card layout */}
      <div className="md:hidden divide-y divide-gray-700">
        {entries.map((entry) => {
          const style = DECISION_STYLE[entry.decision] ?? { label: entry.decision, className: 'text-gray-400 bg-gray-700' }
          return (
            <article key={entry.id} className="px-4 py-3 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-gray-200">{entry.entity_name}</span>
                <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${style.className}`}>
                  {style.label}
                </span>
              </div>
              <div className="text-xs text-gray-400">{entry.reviewer_name} · {formatDateTime(entry.decided_at)}</div>
              <div className="flex items-center gap-2">
                <ConfidenceBadge value={entry.confidence} />
                <span className="text-xs text-gray-500 truncate" title={entry.source_document}>
                  {entry.source_document || '—'}
                </span>
              </div>
            </article>
          )
        })}
      </div>

      {/* Empty state */}
      {entries.length === 0 && (
        <div className="px-4 py-12 text-center text-gray-500">
          <p className="text-lg">暂无审核记录</p>
          <p className="text-sm mt-1">决策记录将在此处显示</p>
        </div>
      )}

      {/* Cursor-based pagination */}
      {(hasNext || hasPrev) && (
        <div className="px-4 py-3 bg-gray-900 border-t border-gray-700 flex items-center justify-between">
          <span className="text-sm text-gray-400">{entries.length} 条</span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              disabled={!hasPrev}
              onClick={onPrev}
              className="px-3 py-1 text-sm rounded border border-gray-600 text-gray-300 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              aria-label="上一页"
            >
              ‹ 上一页
            </button>
            <button
              type="button"
              disabled={!hasNext}
              onClick={onNext}
              className="px-3 py-1 text-sm rounded border border-gray-600 text-gray-300 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              aria-label="下一页"
            >
              下一页 ›
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main component ───────────────────────────────────────────────────

export interface DecisionAuditLogProps {
  /** Override data fetching for testing. */
  readonly initialData?: AuditLogResponse
}

export function DecisionAuditLog({ initialData }: DecisionAuditLogProps) {
  const { filters, cursor, pageSize, decisionOptions, setCursor, setFilters, resetFilters } =
    useAuditLogFilters()

  const [data, setData] = useState<AuditLogResponse | null>(initialData ?? null)
  const [loading, setLoading] = useState(initialData === undefined)
  const [error, setError] = useState<string | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getAuditLog(cursor, pageSize, filters)
      setData(result)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : '加载审核日志失败'
      setError(message)
    } finally {
      setLoading(false)
    }
  }, [cursor, pageSize, filters])

  useEffect(() => {
    if (initialData) return // testing mode — skip fetch
    void loadData()
  }, [loadData, initialData])

  const handleFilterChange = useCallback((update: Partial<Record<string, string | undefined>>) => {
    setFilters(update as Partial<import('@/lib/reference-gaps/types').AuditLogFilters>)
  }, [setFilters])

  const handleNext = useCallback(() => {
    if (!data?.next_cursor) return
    setCursor({ after: data.next_cursor })
  }, [data?.next_cursor, setCursor])

  const handlePrev = useCallback(() => {
    if (!data?.prev_cursor) return
    setCursor({ before: data.prev_cursor })
  }, [data?.prev_cursor, setCursor])

  const entries: ReadonlyArray<AuditEntry> = data?.items ?? []
  const hasNext = data?.has_next ?? false
  const hasPrev = data?.has_prev ?? false

  return (
    <section aria-label="决策审核日志">
      <FilterBar
        filters={filters as Record<string, string | undefined>}
        decisionOptions={decisionOptions}
        onFilterChange={handleFilterChange}
        onReset={resetFilters}
      />

      {error && (
        <div className="mb-4 px-4 py-3 rounded-lg bg-red-900/30 border border-red-700 text-red-300 text-sm">
          {error}
          <button
            type="button"
            onClick={() => void loadData()}
            className="ml-2 underline hover:text-red-200"
            aria-label="重试"
          >
            重试
          </button>
        </div>
      )}

      <div className="relative">
        <AuditTable
          entries={entries}
          hasNext={hasNext}
          hasPrev={hasPrev}
          onNext={handleNext}
          onPrev={handlePrev}
        />

        {/* Loading overlay */}
        {loading && (
          <div className="absolute inset-0 bg-gray-800/60 flex items-center justify-center rounded-lg" role="status" aria-label="加载中">
            <svg
              className="animate-spin h-8 w-8 text-emerald-400"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 8-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          </div>
        )}
      </div>
    </section>
  )
}
