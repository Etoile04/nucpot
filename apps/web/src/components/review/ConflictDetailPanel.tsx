/**
 * ConflictDetailPanel — slide-over panel for side-by-side conflict comparison.
 *
 * Shows source A vs source B with confidence badges and 4 resolution actions.
 * Spec: NFM-848 §3.6, NFM-1006
 */

'use client'

import { useCallback } from 'react'
import { ConfidenceBadge } from '@/components/shared/ConfidenceBadge'
import type {
  ConflictItem,
  ConflictSource,
  ConflictResolutionAction,
} from '@/lib/kg-review-api'

// ── Types ──────────────────────────────────────────────────────────────

interface ConflictDetailPanelProps {
  readonly conflict: ConflictItem | null
  readonly loading?: boolean
  readonly onResolve: (
    conflictId: string,
    action: ConflictResolutionAction,
  ) => void
  readonly onClose: () => void
}

// ── Source Column ────────────────────────────────────────────────────

interface SourceColumnProps {
  readonly source: ConflictSource
  readonly label: string
  readonly accentColor: string
}

function SourceColumn({
  source,
  label,
  accentColor,
}: SourceColumnProps) {
  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700 p-4 space-y-2">
      <div className="flex items-center justify-between">
        <span className={`text-xs font-semibold uppercase tracking-wider ${accentColor}`}>
          {label}
        </span>
        <ConfidenceBadge value={source.confidence} size="sm" />
      </div>
      <p className="text-sm text-gray-400">{source.sourceTitle}</p>
      <p className="text-lg font-semibold text-gray-100">
        {source.value}
        {source.unit != null && (
          <span className="text-sm font-normal text-gray-400 ml-1">
            {source.unit}
          </span>
        )}
      </p>
    </div>
  )
}

// ── Action Config ───────────────────────────────────────────────────

type ActionDef = {
  readonly action: ConflictResolutionAction
  readonly label: string
  readonly className: string
}

const RESOLUTION_ACTIONS: ReadonlyArray<ActionDef> = [
  {
    action: 'keep_a',
    label: '保留版本 A',
    className: 'bg-blue-600 hover:bg-blue-700 text-white',
  },
  {
    action: 'keep_b',
    label: '保留版本 B',
    className: 'bg-amber-600 hover:bg-amber-700 text-white',
  },
  {
    action: 'not_conflict',
    label: '标记非冲突',
    className: 'bg-gray-700 hover:bg-gray-600 text-gray-200',
  },
  {
    action: 'skip',
    label: '跳过',
    className:
      'bg-gray-900 border border-gray-600 text-gray-400 hover:text-gray-200 hover:border-gray-500',
  },
] as const

// ── Panel ─────────────────────────────────────────────────────────────

export function ConflictDetailPanel({
  conflict,
  loading = false,
  onResolve,
  onClose,
}: ConflictDetailPanelProps) {
  const handleResolve = useCallback(
    (action: ConflictResolutionAction) => {
      if (conflict === null) return
      onResolve(conflict.id, action)
    },
    [conflict, onResolve],
  )

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (e.target === e.currentTarget) {
        onClose()
      }
    },
    [onClose],
  )

  if (conflict === null) return null

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end"
      role="dialog"
      aria-modal="true"
      aria-label="冲突详情"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50"
        onClick={handleBackdropClick}
        aria-hidden="true"
      />

      {/* Panel body */}
      <div className="relative w-full max-w-lg bg-gray-800 border-l border-gray-700 shadow-xl overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
          <h2 className="text-lg font-semibold text-gray-100">冲突详情</h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded text-gray-400 hover:text-gray-200 hover:bg-gray-700 transition-colors"
            aria-label="关闭"
          >
            <svg
              className="w-5 h-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>

        {/* Conflict info + side-by-side comparison */}
        <div className="px-6 py-4 space-y-4">
          <div className="space-y-1">
            <h3 className="text-sm font-medium text-gray-300">
              {conflict.entityName}
            </h3>
            <p className="text-xs text-gray-500">
              属性: {conflict.property} · 冲突 #{conflict.conflictNumber}
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <SourceColumn
              source={conflict.sourceA}
              label="版本 A"
              accentColor="text-blue-400"
            />
            <SourceColumn
              source={conflict.sourceB}
              label="版本 B"
              accentColor="text-amber-400"
            />
          </div>
        </div>

        {/* Footer actions */}
        <div className="px-6 py-4 border-t border-gray-700 space-y-3">
          {loading && (
            <div className="flex items-center justify-center py-2">
              <span className="text-gray-400 text-sm animate-pulse">
                处理中…
              </span>
            </div>
          )}
          <div className="grid grid-cols-2 gap-2">
            {RESOLUTION_ACTIONS.map((def) => (
              <button
                key={def.action}
                type="button"
                onClick={() => handleResolve(def.action)}
                disabled={loading}
                className={`px-3 py-2 rounded text-sm font-medium disabled:opacity-50 transition-colors ${def.className}`}
                aria-label={def.label}
              >
                {def.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
